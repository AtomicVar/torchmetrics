# Copyright The Lightning team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from functools import partial

import numpy as np
import pytest
import torch
from scipy.special import expit as sigmoid
from sklearn.metrics import confusion_matrix as sk_confusion_matrix
from torchmetrics.classification.stat_scores import (
    BinaryStatScores,
    MulticlassStatScores,
    MultilabelStatScores,
    StatScores,
)
from torchmetrics.functional.classification.stat_scores import (
    binary_stat_scores,
    multiclass_stat_scores,
    multilabel_stat_scores,
)
from torchmetrics.metric import Metric

from unittests import NUM_CLASSES, THRESHOLD
from unittests.classification.inputs import _binary_cases, _multiclass_cases, _multilabel_cases
from unittests.helpers import seed_all
from unittests.helpers.testers import MetricTester, inject_ignore_index, remove_ignore_index

seed_all(42)


def _sklearn_stat_scores_binary(preds, target, ignore_index, multidim_average):
    if multidim_average == "global":
        preds = preds.view(-1).numpy()
        target = target.view(-1).numpy()
    else:
        preds = preds.numpy()
        target = target.numpy()

    if np.issubdtype(preds.dtype, np.floating):
        if not ((preds > 0) & (preds < 1)).all():
            preds = sigmoid(preds)
        preds = (preds >= THRESHOLD).astype(np.uint8)

    if multidim_average == "global":
        target, preds = remove_ignore_index(target, preds, ignore_index)
        tn, fp, fn, tp = sk_confusion_matrix(y_true=target, y_pred=preds, labels=[0, 1]).ravel()
        return np.array([tp, fp, tn, fn, tp + fn])

    res = []
    for pred, true in zip(preds, target):
        pred = pred.flatten()
        true = true.flatten()
        true, pred = remove_ignore_index(true, pred, ignore_index)
        tn, fp, fn, tp = sk_confusion_matrix(y_true=true, y_pred=pred, labels=[0, 1]).ravel()
        res.append(np.array([tp, fp, tn, fn, tp + fn]))
    return np.stack(res)


@pytest.mark.parametrize("inputs", _binary_cases)
class TestBinaryStatScores(MetricTester):
    """Test class for `BinaryStatScores` metric."""

    @pytest.mark.parametrize("ignore_index", [None, 0, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    @pytest.mark.parametrize("ddp", [pytest.param(True, marks=pytest.mark.DDP), False])
    def test_binary_stat_scores(self, ddp, inputs, ignore_index, multidim_average):
        """Test class implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and preds.ndim < 3:
            pytest.skip("samplewise and non-multidim arrays are not valid")
        if multidim_average == "samplewise" and ddp:
            pytest.skip("samplewise and ddp give different order than non ddp")

        self.run_class_metric_test(
            ddp=ddp,
            preds=preds,
            target=target,
            metric_class=BinaryStatScores,
            reference_metric=partial(
                _sklearn_stat_scores_binary, ignore_index=ignore_index, multidim_average=multidim_average
            ),
            metric_args={"threshold": THRESHOLD, "ignore_index": ignore_index, "multidim_average": multidim_average},
        )

    @pytest.mark.parametrize("ignore_index", [None, 0, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    def test_binary_stat_scores_functional(self, inputs, ignore_index, multidim_average):
        """Test functional implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and preds.ndim < 3:
            pytest.skip("samplewise and non-multidim arrays are not valid")

        self.run_functional_metric_test(
            preds=preds,
            target=target,
            metric_functional=binary_stat_scores,
            reference_metric=partial(
                _sklearn_stat_scores_binary, ignore_index=ignore_index, multidim_average=multidim_average
            ),
            metric_args={
                "threshold": THRESHOLD,
                "ignore_index": ignore_index,
                "multidim_average": multidim_average,
            },
        )

    def test_binary_stat_scores_differentiability(self, inputs):
        """Test the differentiability of the metric, according to its `is_differentiable` attribute."""
        preds, target = inputs
        self.run_differentiability_test(
            preds=preds,
            target=target,
            metric_module=BinaryStatScores,
            metric_functional=binary_stat_scores,
            metric_args={"threshold": THRESHOLD},
        )

    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_binary_stat_scores_dtype_cpu(self, inputs, dtype):
        """Test dtype support of the metric on CPU."""
        preds, target = inputs
        if (preds < 0).any() and dtype == torch.half:
            pytest.xfail(reason="torch.sigmoid in metric does not support cpu + half precision")
        self.run_precision_test_cpu(
            preds=preds,
            target=target,
            metric_module=BinaryStatScores,
            metric_functional=binary_stat_scores,
            metric_args={"threshold": THRESHOLD},
            dtype=dtype,
        )

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="test requires cuda")
    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_binary_stat_scores_dtype_gpu(self, inputs, dtype):
        """Test dtype support of the metric on GPU."""
        preds, target = inputs
        self.run_precision_test_gpu(
            preds=preds,
            target=target,
            metric_module=BinaryStatScores,
            metric_functional=binary_stat_scores,
            metric_args={"threshold": THRESHOLD},
            dtype=dtype,
        )


def _sklearn_stat_scores_multiclass_global(preds, target, ignore_index, average):
    preds = preds.numpy().flatten()
    target = target.numpy().flatten()
    target, preds = remove_ignore_index(target, preds, ignore_index)
    confmat = sk_confusion_matrix(y_true=target, y_pred=preds, labels=list(range(NUM_CLASSES)))
    tp = np.diag(confmat)
    fp = confmat.sum(0) - tp
    fn = confmat.sum(1) - tp
    tn = confmat.sum() - (fp + fn + tp)

    res = np.stack([tp, fp, tn, fn, tp + fn], 1)
    if average == "micro":
        return res.sum(0)
    if average == "macro":
        return res.mean(0)
    if average == "weighted":
        w = tp + fn
        return (res * (w / w.sum()).reshape(-1, 1)).sum(0)
    if average is None or average == "none":
        return res
    return None


def _sklearn_stat_scores_multiclass_local(preds, target, ignore_index, average):
    preds = preds.numpy()
    target = target.numpy()

    res = []
    for pred, true in zip(preds, target):
        pred = pred.flatten()
        true = true.flatten()
        true, pred = remove_ignore_index(true, pred, ignore_index)
        confmat = sk_confusion_matrix(y_true=true, y_pred=pred, labels=list(range(NUM_CLASSES)))
        tp = np.diag(confmat)
        fp = confmat.sum(0) - tp
        fn = confmat.sum(1) - tp
        tn = confmat.sum() - (fp + fn + tp)
        r = np.stack([tp, fp, tn, fn, tp + fn], 1)
        if average == "micro":
            res.append(r.sum(0))
        elif average == "macro":
            res.append(r.mean(0))
        elif average == "weighted":
            w = tp + fn
            res.append((r * (w / w.sum()).reshape(-1, 1)).sum(0))
        elif average is None or average == "none":
            res.append(r)
    return np.stack(res, 0)


def _sklearn_stat_scores_multiclass(preds, target, ignore_index, multidim_average, average):
    if preds.ndim == target.ndim + 1:
        preds = torch.argmax(preds, 1)
    if multidim_average == "global":
        return _sklearn_stat_scores_multiclass_global(preds, target, ignore_index, average)
    return _sklearn_stat_scores_multiclass_local(preds, target, ignore_index, average)


@pytest.mark.parametrize("inputs", _multiclass_cases)
class TestMulticlassStatScores(MetricTester):
    """Test class for `MulticlassStatScores` metric."""

    @pytest.mark.parametrize("ignore_index", [None, 0, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    @pytest.mark.parametrize("average", ["micro", "macro", None])
    @pytest.mark.parametrize("ddp", [pytest.param(True, marks=pytest.mark.DDP), False])
    def test_multiclass_stat_scores(self, ddp, inputs, ignore_index, multidim_average, average):
        """Test class implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and target.ndim < 3:
            pytest.skip("samplewise and non-multidim arrays are not valid")
        if multidim_average == "samplewise" and ddp:
            pytest.skip("samplewise and ddp give different order than non ddp")

        self.run_class_metric_test(
            ddp=ddp,
            preds=preds,
            target=target,
            metric_class=MulticlassStatScores,
            reference_metric=partial(
                _sklearn_stat_scores_multiclass,
                ignore_index=ignore_index,
                multidim_average=multidim_average,
                average=average,
            ),
            metric_args={
                "ignore_index": ignore_index,
                "multidim_average": multidim_average,
                "average": average,
                "num_classes": NUM_CLASSES,
            },
        )

    @pytest.mark.parametrize("ignore_index", [None, 0, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    @pytest.mark.parametrize("average", ["micro", "macro", None])
    def test_multiclass_stat_scores_functional(self, inputs, ignore_index, multidim_average, average):
        """Test functional implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and target.ndim < 3:
            pytest.skip("samplewise and non-multidim arrays are not valid")

        self.run_functional_metric_test(
            preds=preds,
            target=target,
            metric_functional=multiclass_stat_scores,
            reference_metric=partial(
                _sklearn_stat_scores_multiclass,
                ignore_index=ignore_index,
                multidim_average=multidim_average,
                average=average,
            ),
            metric_args={
                "ignore_index": ignore_index,
                "multidim_average": multidim_average,
                "average": average,
                "num_classes": NUM_CLASSES,
            },
        )

    def test_multiclass_stat_scores_differentiability(self, inputs):
        """Test the differentiability of the metric, according to its `is_differentiable` attribute."""
        preds, target = inputs
        self.run_differentiability_test(
            preds=preds,
            target=target,
            metric_module=MulticlassStatScores,
            metric_functional=multiclass_stat_scores,
            metric_args={"num_classes": NUM_CLASSES},
        )

    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_multiclass_stat_scores_dtype_cpu(self, inputs, dtype):
        """Test dtype support of the metric on CPU."""
        preds, target = inputs
        if (preds < 0).any() and dtype == torch.half:
            pytest.xfail(reason="torch.sigmoid in metric does not support cpu + half precision")
        self.run_precision_test_cpu(
            preds=preds,
            target=target,
            metric_module=MulticlassStatScores,
            metric_functional=multiclass_stat_scores,
            metric_args={"num_classes": NUM_CLASSES},
            dtype=dtype,
        )

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="test requires cuda")
    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_multiclass_stat_scores_dtype_gpu(self, inputs, dtype):
        """Test dtype support of the metric on GPU."""
        preds, target = inputs
        self.run_precision_test_gpu(
            preds=preds,
            target=target,
            metric_module=MulticlassStatScores,
            metric_functional=multiclass_stat_scores,
            metric_args={"num_classes": NUM_CLASSES},
            dtype=dtype,
        )


_mc_k_target = torch.tensor([0, 1, 2])
_mc_k_preds = torch.tensor([[0.35, 0.4, 0.25], [0.1, 0.5, 0.4], [0.2, 0.1, 0.7]])


@pytest.mark.parametrize(
    ("k", "preds", "target", "average", "expected"),
    [
        (1, _mc_k_preds, _mc_k_target, "micro", torch.tensor([2, 1, 5, 1, 3])),
        (2, _mc_k_preds, _mc_k_target, "micro", torch.tensor([3, 3, 3, 0, 3])),
        (1, _mc_k_preds, _mc_k_target, None, torch.tensor([[0, 1, 1], [0, 1, 0], [2, 1, 2], [1, 0, 0], [1, 1, 1]])),
        (2, _mc_k_preds, _mc_k_target, None, torch.tensor([[1, 1, 1], [1, 1, 1], [1, 1, 1], [0, 0, 0], [1, 1, 1]])),
    ],
)
def test_top_k_multiclass(k, preds, target, average, expected):
    """A simple test to check that top_k works as expected."""
    class_metric = MulticlassStatScores(top_k=k, average=average, num_classes=3)
    class_metric.update(preds, target)

    assert torch.allclose(class_metric.compute().long(), expected.T)
    assert torch.allclose(
        multiclass_stat_scores(preds, target, top_k=k, average=average, num_classes=3).long(), expected.T
    )


def test_top_k_ignore_index_multiclass():
    """Test that top_k argument works together with ignore_index."""
    preds_without = torch.randn(10, 3).softmax(dim=-1)
    target_without = torch.randint(3, (10,))
    preds_with = torch.cat([preds_without, torch.randn(10, 3).softmax(dim=-1)], 0)
    target_with = torch.cat([target_without, -100 * torch.ones(10)], 0).long()

    res_without = multiclass_stat_scores(preds_without, target_without, num_classes=3, average="micro", top_k=2)
    res_with = multiclass_stat_scores(
        preds_with, target_with, num_classes=3, average="micro", top_k=2, ignore_index=-100
    )

    assert torch.allclose(res_without, res_with)


def test_multiclass_overflow():
    """Test that multiclass computations does not overflow even on byte input."""
    preds = torch.randint(20, (100,)).byte()
    target = torch.randint(20, (100,)).byte()

    m = MulticlassStatScores(num_classes=20, average=None)
    res = m(preds, target)

    confmat = sk_confusion_matrix(target, preds)
    fp = confmat.sum(axis=0) - np.diag(confmat)
    fn = confmat.sum(axis=1) - np.diag(confmat)
    tp = np.diag(confmat)
    tn = confmat.sum() - (fp + fn + tp)
    compare = np.stack([tp, fp, tn, fn, tp + fn]).T

    assert torch.allclose(res, torch.tensor(compare))


def _sklearn_stat_scores_multilabel(preds, target, ignore_index, multidim_average, average):
    preds = preds.numpy()
    target = target.numpy()
    if np.issubdtype(preds.dtype, np.floating):
        if not ((preds > 0) & (preds < 1)).all():
            preds = sigmoid(preds)
        preds = (preds >= THRESHOLD).astype(np.uint8)
    preds = preds.reshape(*preds.shape[:2], -1)
    target = target.reshape(*target.shape[:2], -1)
    if multidim_average == "global":
        stat_scores = []
        for i in range(preds.shape[1]):
            pred, true = preds[:, i].flatten(), target[:, i].flatten()
            true, pred = remove_ignore_index(true, pred, ignore_index)
            tn, fp, fn, tp = sk_confusion_matrix(true, pred, labels=[0, 1]).ravel()
            stat_scores.append(np.array([tp, fp, tn, fn, tp + fn]))
        res = np.stack(stat_scores, axis=0)

        if average == "micro":
            return res.sum(0)
        if average == "macro":
            return res.mean(0)
        if average == "weighted":
            w = res[:, 0] + res[:, 3]
            return (res * (w / w.sum()).reshape(-1, 1)).sum(0)
        if average is None or average == "none":
            return res
        return None

    stat_scores = []
    for i in range(preds.shape[0]):
        scores = []
        for j in range(preds.shape[1]):
            pred, true = preds[i, j], target[i, j]
            true, pred = remove_ignore_index(true, pred, ignore_index)
            tn, fp, fn, tp = sk_confusion_matrix(true, pred, labels=[0, 1]).ravel()
            scores.append(np.array([tp, fp, tn, fn, tp + fn]))
        stat_scores.append(np.stack(scores, 1))
    res = np.stack(stat_scores, 0)
    if average == "micro":
        return res.sum(-1)
    if average == "macro":
        return res.mean(-1)
    if average == "weighted":
        w = res[:, 0, :] + res[:, 3, :]
        return (res * (w / w.sum())[:, np.newaxis]).sum(-1)
    if average is None or average == "none":
        return np.moveaxis(res, 1, -1)
    return None


@pytest.mark.parametrize("inputs", _multilabel_cases)
class TestMultilabelStatScores(MetricTester):
    """Test class for `MultilabelStatScores` metric."""

    @pytest.mark.parametrize("ddp", [pytest.param(True, marks=pytest.mark.DDP), False])
    @pytest.mark.parametrize("ignore_index", [None, 0, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    @pytest.mark.parametrize("average", ["micro", "macro", None])
    def test_multilabel_stat_scores(self, ddp, inputs, ignore_index, multidim_average, average):
        """Test class implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and preds.ndim < 4:
            pytest.skip("samplewise and non-multidim arrays are not valid")
        if multidim_average == "samplewise" and ddp:
            pytest.skip("samplewise and ddp give different order than non ddp")

        self.run_class_metric_test(
            ddp=ddp,
            preds=preds,
            target=target,
            metric_class=MultilabelStatScores,
            reference_metric=partial(
                _sklearn_stat_scores_multilabel,
                ignore_index=ignore_index,
                multidim_average=multidim_average,
                average=average,
            ),
            metric_args={
                "num_labels": NUM_CLASSES,
                "threshold": THRESHOLD,
                "ignore_index": ignore_index,
                "multidim_average": multidim_average,
                "average": average,
            },
        )

    @pytest.mark.parametrize("ignore_index", [None, 0, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    @pytest.mark.parametrize("average", ["micro", "macro", None])
    def test_multilabel_stat_scores_functional(self, inputs, ignore_index, multidim_average, average):
        """Test functional implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and preds.ndim < 4:
            pytest.skip("samplewise and non-multidim arrays are not valid")

        self.run_functional_metric_test(
            preds=preds,
            target=target,
            metric_functional=multilabel_stat_scores,
            reference_metric=partial(
                _sklearn_stat_scores_multilabel,
                ignore_index=ignore_index,
                multidim_average=multidim_average,
                average=average,
            ),
            metric_args={
                "num_labels": NUM_CLASSES,
                "threshold": THRESHOLD,
                "ignore_index": ignore_index,
                "multidim_average": multidim_average,
                "average": average,
            },
        )

    def test_multilabel_stat_scores_differentiability(self, inputs):
        """Test the differentiability of the metric, according to its `is_differentiable` attribute."""
        preds, target = inputs
        self.run_differentiability_test(
            preds=preds,
            target=target,
            metric_module=MultilabelStatScores,
            metric_functional=multilabel_stat_scores,
            metric_args={"num_labels": NUM_CLASSES, "threshold": THRESHOLD},
        )

    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_multilabel_stat_scores_dtype_cpu(self, inputs, dtype):
        """Test dtype support of the metric on CPU."""
        preds, target = inputs
        if (preds < 0).any() and dtype == torch.half:
            pytest.xfail(reason="torch.sigmoid in metric does not support cpu + half precision")
        self.run_precision_test_cpu(
            preds=preds,
            target=target,
            metric_module=MultilabelStatScores,
            metric_functional=multilabel_stat_scores,
            metric_args={"num_labels": NUM_CLASSES, "threshold": THRESHOLD},
            dtype=dtype,
        )

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="test requires cuda")
    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_multilabel_stat_scores_dtype_gpu(self, inputs, dtype):
        """Test dtype support of the metric on GPU."""
        preds, target = inputs
        self.run_precision_test_gpu(
            preds=preds,
            target=target,
            metric_module=MultilabelStatScores,
            metric_functional=multilabel_stat_scores,
            metric_args={"num_labels": NUM_CLASSES, "threshold": THRESHOLD},
            dtype=dtype,
        )


def test_support_for_int():
    """See issue: https://github.com/Lightning-AI/torchmetrics/issues/1970."""
    metric = MulticlassStatScores(num_classes=4, average="none", multidim_average="samplewise", ignore_index=0)
    prediction = torch.randint(low=0, high=4, size=(1, 224, 224)).to(torch.uint8)
    label = torch.randint(low=0, high=4, size=(1, 224, 224)).to(torch.uint8)
    score = metric(preds=prediction, target=label)
    assert score.shape == (1, 4, 5)


@pytest.mark.parametrize(
    ("metric", "kwargs"),
    [
        (BinaryStatScores, {"task": "binary"}),
        (MulticlassStatScores, {"task": "multiclass", "num_classes": 3}),
        (MultilabelStatScores, {"task": "multilabel", "num_labels": 3}),
        (None, {"task": "not_valid_task"}),
    ],
)
def test_wrapper_class(metric, kwargs, base_metric=StatScores):
    """Test the wrapper class."""
    assert issubclass(base_metric, Metric)
    if metric is None:
        with pytest.raises(ValueError, match=r"Invalid *"):
            base_metric(**kwargs)
    else:
        instance = base_metric(**kwargs)
        assert isinstance(instance, metric)
        assert isinstance(instance, Metric)
