from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import numpy as np

from hypothesis import given
import hypothesis.strategies as st

from caffe2.python import core
import caffe2.python.hypothesis_test_util as hu


class TestClip(hu.HypothesisTestCase):
    @given(X=hu.tensor(),
           min_=st.floats(min_value=-1, max_value=0),
           max_=st.floats(min_value=0, max_value=1),
           inplace=st.booleans(),
           **hu.gcs)
    def test_clip(self, X, min_, max_, inplace, gc, dc):
        # go away from the origin point to avoid kink problems

        X[np.abs(X - min_) < 0.05] += 0.1
        X[np.abs(X - max_) < 0.05] += 0.1

        def clip_ref(X):
            X = X.clip(min_, max_)
            return (X,)

        op = core.CreateOperator(
            "Clip",
            ["X"], ["Y" if not inplace else "X"],
            min=min_,
            max=max_)
        self.assertReferenceChecks(gc, op, [X], clip_ref)
        # Check over multiple devices
        self.assertDeviceChecks(dc, op, [X], [0])
        # Gradient check wrt X
        self.assertGradientChecks(gc, op, [X], 0, [0])


if __name__ == "__main__":
    import unittest
    unittest.main()
