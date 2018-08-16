from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import unittest

from hypothesis import given
import numpy as np

from caffe2.proto import caffe2_pb2
from caffe2.python import core, workspace
import caffe2.python.hypothesis_test_util as hu


# TODO(jiayq): make them hypothesis tests for better coverage.
class TestElementwiseBroadcast(hu.HypothesisTestCase):
    @given(**hu.gcs)
    def test_broadcast_Add(self, gc, dc):
        # Set broadcast and no axis, i.e. broadcasting last dimensions.
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(4, 5).astype(np.float32)
        op = core.CreateOperator("Add", ["X", "Y"], "out", broadcast=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(out, X + Y)
        self.assertDeviceChecks(dc, op, [X, Y], [0])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])

        # broadcasting intermediate dimensions
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(3, 4).astype(np.float32)
        op = core.CreateOperator("Add", ["X", "Y"], "out", broadcast=1, axis=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(out, X + Y[:, :, np.newaxis])
        self.assertDeviceChecks(dc, op, [X, Y], [0])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])

        # broadcasting the first dimension
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(2).astype(np.float32)
        op = core.CreateOperator("Add", ["X", "Y"], "out", broadcast=1, axis=0)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(
            out, X + Y[:, np.newaxis, np.newaxis, np.newaxis])
        self.assertDeviceChecks(dc, op, [X, Y], [0])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])

        # broadcasting with single elem dimensions at both ends
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(1, 4, 1).astype(np.float32)
        op = core.CreateOperator("Add", ["X", "Y"], "out", broadcast=1, axis=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(out, X + Y)
        self.assertDeviceChecks(dc, op, [X, Y], [0])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])

    @given(**hu.gcs)
    def test_broadcast_Mul(self, gc, dc):
        # Set broadcast and no axis, i.e. broadcasting last dimensions.
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(4, 5).astype(np.float32)
        op = core.CreateOperator("Mul", ["X", "Y"], "out", broadcast=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(out, X * Y)
        self.assertDeviceChecks(dc, op, [X, Y], [0])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])

        # broadcasting intermediate dimensions
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(3, 4).astype(np.float32)
        op = core.CreateOperator("Mul", ["X", "Y"], "out", broadcast=1, axis=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(out, X * Y[:, :, np.newaxis])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])
        self.assertDeviceChecks(dc, op, [X, Y], [0])

        # broadcasting the first dimension
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(2).astype(np.float32)
        op = core.CreateOperator("Mul", ["X", "Y"], "out", broadcast=1, axis=0)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(
            out, X * Y[:, np.newaxis, np.newaxis, np.newaxis])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])
        self.assertDeviceChecks(dc, op, [X, Y], [0])

        # broadcasting with single elem dimensions at both ends
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(1, 4, 1).astype(np.float32)
        op = core.CreateOperator("Mul", ["X", "Y"], "out", broadcast=1, axis=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(out, X * Y)
        self.assertDeviceChecks(dc, op, [X, Y], [0])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])

    @given(**hu.gcs)
    def test_broadcast_Sub(self, gc, dc):
        # Set broadcast and no axis, i.e. broadcasting last dimensions.
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(4, 5).astype(np.float32)
        op = core.CreateOperator("Sub", ["X", "Y"], "out", broadcast=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(out, X - Y)
        self.assertDeviceChecks(dc, op, [X, Y], [0])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])

        # broadcasting intermediate dimensions
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(3, 4).astype(np.float32)
        op = core.CreateOperator("Sub", ["X", "Y"], "out", broadcast=1, axis=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(out, X - Y[:, :, np.newaxis])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])
        self.assertDeviceChecks(dc, op, [X, Y], [0])

        # broadcasting the first dimension
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(2).astype(np.float32)
        op = core.CreateOperator("Sub", ["X", "Y"], "out", broadcast=1, axis=0)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(
            out, X - Y[:, np.newaxis, np.newaxis, np.newaxis])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])
        self.assertDeviceChecks(dc, op, [X, Y], [0])

        # broadcasting with single elem dimensions at both ends
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(1, 4, 1).astype(np.float32)
        op = core.CreateOperator("Sub", ["X", "Y"], "out", broadcast=1, axis=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(out, X - Y)
        self.assertDeviceChecks(dc, op, [X, Y], [0])
        self.assertGradientChecks(gc, op, [X, Y], 1, [0])

    @given(**hu.gcs)
    def test_broadcast_powt(self, gc, dc):
        np.random.seed(101)

        #operator
        def powt_op(X, Y):
            return [np.power(X, Y)]

        #two gradients Y*X^(Y-1) and X^Y * ln(X)
        def powt_grad(g_out, outputs, fwd_inputs):
            [X, Y] = fwd_inputs
            Z = outputs[0]
            return ([Y * np.power(X, Y - 1), Z * np.log(X)] * g_out)

        #1. Set broadcast and no axis, i.e. broadcasting last dimensions.
        X = np.random.rand(2, 3, 4, 5).astype(np.float32) + 1.0
        Y = np.random.rand(4, 5).astype(np.float32) + 2.0

        #two gradients Y*X^(Y-1) and X^Y * ln(X)
        #latter gradient is sumed over 1 and 0 dims to account for broadcast
        def powt_grad_broadcast(g_out, outputs, fwd_inputs):
            [GX, GY] = powt_grad(g_out, outputs, fwd_inputs)
            return ([GX, np.sum(np.sum(GY, 1), 0)])

        op = core.CreateOperator("Pow", ["X", "Y"], "Z", broadcast=1)
        self.assertReferenceChecks(device_option=gc,
                                   op=op,
                                   inputs=[X, Y],
                                   reference=powt_op,
                                   output_to_grad="Z",
                                   grad_reference=powt_grad_broadcast)

        #2. broadcasting intermediate dimensions
        X = np.random.rand(2, 3, 4, 5).astype(np.float32) + 1.0
        Y = np.random.rand(3, 4).astype(np.float32) + 2.0

        #pow op with the latter array increased by one dim
        def powt_op_axis1(X, Y):
            return powt_op(X, Y[:, :, np.newaxis])

        #two gradients Y*X^(Y-1) and X^Y * ln(X)
        #latter gradient is sumed over 3 and 0 dims to account for broadcast
        def powt_grad_axis1(g_out, outputs, fwd_inputs):
            [X, Y] = fwd_inputs
            [GX, GY] = powt_grad(g_out, outputs, [X, Y[:, :, np.newaxis]])
            return ([GX, np.sum(np.sum(GY, 3), 0)])

        op = core.CreateOperator("Pow", ["X", "Y"], "Z", broadcast=1, axis=1)
        self.assertReferenceChecks(device_option=gc,
                                   op=op,
                                   inputs=[X, Y],
                                   reference=powt_op_axis1,
                                   output_to_grad="Z",
                                   grad_reference=powt_grad_axis1)

        #3. broadcasting the first dimension
        X = np.random.rand(2, 3, 4, 5).astype(np.float32) + 1.0
        Y = np.random.rand(2).astype(np.float32) + 2.0

        #pow op with the latter array increased by one dim
        def powt_op_axis0(X, Y):
            return powt_op(X, Y[:, np.newaxis, np.newaxis, np.newaxis])

        #two gradients Y*X^(Y-1) and X^Y * ln(X)
        #latter gradient is sumed over 3, 2 and 1 dims to account for broadcast
        def powt_grad_axis0(g_out, outputs, fwd_inputs):
            [X, Y] = fwd_inputs
            [GX, GY] = powt_grad(g_out,
                                 outputs,
                                 [X, Y[:, np.newaxis, np.newaxis, np.newaxis]])
            return ([GX, np.sum(np.sum(np.sum(GY, 3), 2), 1)])

        op = core.CreateOperator("Pow", ["X", "Y"], "Z", broadcast=1, axis=0)
        self.assertReferenceChecks(device_option=gc,
                                   op=op,
                                   inputs=[X, Y],
                                   reference=powt_op_axis0,
                                   output_to_grad="Z",
                                   grad_reference=powt_grad_axis0)

        #4. broadcasting with single elem dimensions at both ends
        X = np.random.rand(2, 3, 4, 5).astype(np.float32) + 1.0
        Y = np.random.rand(1, 4, 1).astype(np.float32) + 2.0

        #pow op with the latter array increased by one dim
        def powt_op_mixed(X, Y):
            return powt_op(X, Y[np.newaxis, :, :, :])

        #two gradients Y*X^(Y-1) and X^Y * ln(X)
        #latter gradient is sumed over 0 and 1 dims to account for broadcast
        def powt_grad_mixed(g_out, outputs, fwd_inputs):
            [X, Y] = fwd_inputs
            [GX, GY] = powt_grad(g_out, outputs, [X, Y[np.newaxis, :, :, :]])
            return ([GX, np.reshape(np.sum(np.sum(np.sum(GY, 3), 1), 0),
                                    (1, 4, 1))])

        op = core.CreateOperator("Pow", ["X", "Y"], "Z", broadcast=1, axis=1)
        self.assertReferenceChecks(device_option=gc,
                                   op=op,
                                   inputs=[X, Y],
                                   reference=powt_op_mixed,
                                   output_to_grad="Z",
                                   grad_reference=powt_grad_mixed)

    @given(**hu.gcs)
    def test_broadcast_scalar(self, gc, dc):
        # broadcasting constant
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(1).astype(np.float32)
        op = core.CreateOperator("Add", ["X", "Y"], "out", broadcast=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(
            out, X + Y)
        self.assertDeviceChecks(dc, op, [X, Y], [0])

        # broadcasting scalar
        X = np.random.rand(1).astype(np.float32)
        Y = np.random.rand(1).astype(np.float32).reshape([])
        op = core.CreateOperator("Add", ["X", "Y"], "out", broadcast=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(
            out, X + Y)
        self.assertDeviceChecks(dc, op, [X, Y], [0])

    @given(**hu.gcs)
    def test_semantic_broadcast(self, gc, dc):
        # NCHW as default
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(3).astype(np.float32)
        op = core.CreateOperator(
            "Add", ["X", "Y"], "out", broadcast=1, axis_str="C")
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(
            out, X + Y[:, np.newaxis, np.newaxis])
        self.assertDeviceChecks(dc, op, [X, Y], [0])

        # NHWC
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(5).astype(np.float32)
        op = core.CreateOperator(
            "Add", ["X", "Y"], "out", broadcast=1, axis_str="C", order="NHWC")
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        np.testing.assert_array_almost_equal(out, X + Y)
        self.assertDeviceChecks(dc, op, [X, Y], [0])

    @given(**hu.gcs)
    def test_sum_reduce(self, gc, dc):
        # Set broadcast and no axis, i.e. broadcasting last dimensions.
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(4, 5).astype(np.float32)
        op = core.CreateOperator(
            "SumReduceLike", ["X", "Y"], "out", broadcast=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        res = np.sum(X, axis=0)
        res = np.sum(res, axis=0)
        np.testing.assert_array_almost_equal(out, res)
        self.assertDeviceChecks(dc, op, [X, Y], [0])

        # Set broadcast and no axis, i.e. broadcasting last dimensions.
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(2, 3).astype(np.float32)
        op = core.CreateOperator(
            "SumReduceLike", ["X", "Y"], "out", broadcast=1, axis=0)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        res = np.sum(X, axis=3)
        res = np.sum(res, axis=2)
        np.testing.assert_array_almost_equal(out, res, decimal=3)
        self.assertDeviceChecks(dc, op, [X, Y], [0])

        # broadcasting intermediate dimensions
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(3, 4).astype(np.float32)
        op = core.CreateOperator(
            "SumReduceLike", ["X", "Y"], "out", broadcast=1, axis=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        res = np.sum(X, axis=0)
        res = np.sum(res, axis=2)
        np.testing.assert_array_almost_equal(out, res)
        self.assertDeviceChecks(dc, op, [X, Y], [0])

        # broadcasting intermediate dimensions
        X = np.random.rand(2, 3, 4, 500).astype(np.float64)
        Y = np.random.rand(1).astype(np.float64)
        op = core.CreateOperator(
            "SumReduceLike", ["X", "Y"], "out", broadcast=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        res = np.array(np.sum(X))
        np.testing.assert_array_almost_equal(out, res, decimal=0)

        # broadcasting with single elem dimensions at both ends
        X = np.random.rand(2, 3, 4, 5).astype(np.float32)
        Y = np.random.rand(1, 3, 4, 1).astype(np.float32)
        op = core.CreateOperator(
            "SumReduceLike", ["X", "Y"], "out", broadcast=1)
        workspace.FeedBlob("X", X)
        workspace.FeedBlob("Y", Y)
        workspace.RunOperatorOnce(op)
        out = workspace.FetchBlob("out")
        res = np.sum(X, axis=0)
        res = np.sum(res, axis=2).reshape(Y.shape)
        np.testing.assert_array_almost_equal(out, res)
        self.assertDeviceChecks(dc, op, [X, Y], [0])

        # fp64 is not supported with the CUDA op
        dc_cpu_only = [d for d in dc if d.device_type != caffe2_pb2.CUDA]
        self.assertDeviceChecks(dc_cpu_only, op, [X, Y], [0])

    @unittest.skipIf(not workspace.has_gpu_support, "No gpu support")
    @given(**hu.gcs_gpu_only)
    def test_sum_reduce_fp16(self, gc, dc):
        # Set broadcast and no axis, i.e. broadcasting last dimensions.
        X = np.random.rand(2, 3, 4, 5).astype(np.float16)
        Y = np.random.rand(4, 5).astype(np.float16)
        op = core.CreateOperator(
            "SumReduceLike", ["X", "Y"], "out", broadcast=1, device_option=gc)

        def ref_op(X, Y):
            res = np.sum(X, axis=0)
            res = np.sum(res, axis=0)
            return [res]

        self.assertReferenceChecks(
            device_option=gc,
            op=op,
            inputs=[X, Y],
            reference=ref_op,
            threshold=1e-3)

        # Set broadcast and no axis, i.e. broadcasting last dimensions.
        X = np.random.rand(2, 3, 4, 5).astype(np.float16)
        Y = np.random.rand(2, 3).astype(np.float16)
        op = core.CreateOperator(
            "SumReduceLike", ["X", "Y"], "out", broadcast=1, axis=0)

        def ref_op(X, Y):
            res = np.sum(X, axis=3)
            res = np.sum(res, axis=2)
            return [res]

        self.assertReferenceChecks(
            device_option=gc,
            op=op,
            inputs=[X, Y],
            reference=ref_op,
            threshold=1e-3)

        # broadcasting intermediate dimensions
        X = np.random.rand(2, 3, 4, 5).astype(np.float16)
        Y = np.random.rand(3, 4).astype(np.float16)
        op = core.CreateOperator(
            "SumReduceLike", ["X", "Y"], "out", broadcast=1, axis=1)

        def ref_op(X, Y):
            res = np.sum(X, axis=0)
            res = np.sum(res, axis=2)
            return [res]

        self.assertReferenceChecks(
            device_option=gc,
            op=op,
            inputs=[X, Y],
            reference=ref_op,
            threshold=1e-3)

        # broadcasting with single elem dimensions at both ends
        X = np.random.rand(2, 3, 4, 5).astype(np.float16)
        Y = np.random.rand(1, 3, 4, 1).astype(np.float16)
        op = core.CreateOperator(
            "SumReduceLike", ["X", "Y"], "out", broadcast=1)

        def ref_op(X, Y):
            res = np.sum(X, axis=0)
            res = np.sum(res, axis=2)
            return [res.reshape(Y.shape)]

        self.assertReferenceChecks(
            device_option=gc,
            op=op,
            inputs=[X, Y],
            reference=ref_op,
            threshold=1e-3)

if __name__ == "__main__":
    unittest.main()
