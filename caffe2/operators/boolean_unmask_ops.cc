#include "caffe2/operators/boolean_unmask_ops.h"
#include "caffe2/core/operator.h"
#include "caffe2/core/tensor.h"

namespace caffe2 {

template <>
bool BooleanUnmaskOp<CPUContext>::RunOnDevice() {
  int maskSize = Input(0).size();
  int numMasks = InputSize() / 2;
  auto& valueMeta = Input(1).meta();

  auto* valuesOut = Output(0);
  valuesOut->Resize(maskSize);
  auto* valuesOutPtr = (char*)valuesOut->raw_mutable_data(valueMeta);

  std::vector<int> nextValueIndices(numMasks, 0);
  for (int maskOffset = 0; maskOffset < maskSize; ++maskOffset) {
    bool maskFound = false;
    for (int maskIndex = 0; maskIndex < numMasks; ++maskIndex) {
      auto& mask = Input(maskIndex * 2);
      CAFFE_ENFORCE_EQ(mask.ndim(), 1);
      CAFFE_ENFORCE_EQ(mask.size(), maskSize);
      const auto* maskPtr = mask.template data<bool>();

      auto& values = Input(maskIndex * 2 + 1);
      CAFFE_ENFORCE_EQ(values.ndim(), 1);
      const auto* valuesPtr = (char*)values.raw_data();

      if (maskPtr[maskOffset]) {
        auto& valueIndex = nextValueIndices[maskIndex];
        CAFFE_ENFORCE_LT(valueIndex, values.size());
        auto* src = valuesPtr + (valueIndex++) * valueMeta.itemsize();
        auto* dst = valuesOutPtr + maskOffset * valueMeta.itemsize();
        std::copy(src, src + valueMeta.itemsize(), dst);
        maskFound = true;
        break;
      }
    }
    CAFFE_ENFORCE(
        maskFound, "All masks have False at position ", maskOffset, ".");
  }
  // check all indices match value length
  for (int i = 0; i < numMasks; ++i) {
    auto& values = Input(i * 2 + 1);
    CAFFE_ENFORCE_EQ(
        values.size(),
        nextValueIndices[i],
        "The number of true at mask ",
        i,
        " does not match the corresponding value size.");
  }
  return true;
}

REGISTER_CPU_OPERATOR(BooleanUnmask, BooleanUnmaskOp<CPUContext>);

OPERATOR_SCHEMA(BooleanUnmask)
    .NumInputs([](int n) { return n > 0 && n % 2 == 0; })
    .NumOutputs(1)
    .SetDoc(R"DOC(
Given a series of mask and values, reconstruct values together according
to masks.

A comprehensive example:
  mask1   = True, False, True, False, False
  values1 = 1.0, 3.0
  mask2   = False, True, False, False, False
  values2 = 2.0
  mask3   = False, False, False, True, True
  values3 = 4.0, 5.0

Reconstruct by:
  output = net.BooleanUnmask([mask1, values1, mask2, values2, mask3, values3], ["output"])

We get:
  output = 1.0, 2.0, 3.0, 4.0, 5.0

Note that for all mask positions, there must be at least one True. If for a
field there are multiple True's, we will accept the first value. For example:


Example 1:
  mask1   = True, False
  values1 = 1.0
  mask2   = False, False
  values2 =

This is not allowed:
  output = net.BooleanUnmask([mask1, values1, mask2, values2], ["output"])

Example 2:
  mask1   = True, False
  values1 = 1.0
  mask2   = True, True
  values2 = 2.0, 2.0

  output = net.BooleanUnmask([mask1, values1, mask2, values2], ["output"])

We get:
  output = 1.0, 2.0
)DOC")
    .Output(0, "unmasked_data", "The final reconstructed unmasked data");

NO_GRADIENT(BooleanUnmask)
}
