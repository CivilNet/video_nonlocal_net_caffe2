#include <iostream>

#include "caffe2/core/operator.h"
#include "caffe2/operators/operator_fallback_gpu.h"
#include <gtest/gtest.h>

namespace caffe2 {


class IncrementByOneOp final : public Operator<CPUContext> {
 public:
  IncrementByOneOp(const OperatorDef& def, Workspace* ws)
      : Operator<CPUContext>(def, ws) {}
  bool RunOnDevice() {
    const auto& in = Input(0);
    auto* out = Output(0);
    out->ResizeLike(in);
    const float* in_data = in.template data<float>();
    float* out_data = out->template mutable_data<float>();
    for (int i = 0; i < in.size(); ++i) {
      out_data[i] = in_data[i] + 1.f;
    }
    return true;
  }
};


OPERATOR_SCHEMA(IncrementByOne)
    .NumInputs(1).NumOutputs(1).AllowInplace({{0, 0}});

REGISTER_CPU_OPERATOR(IncrementByOne, IncrementByOneOp);
REGISTER_CUDA_OPERATOR(IncrementByOne,
                       GPUFallbackOp<IncrementByOneOp>);

TEST(OperatorFallbackTest, IncrementByOneOp) {
  OperatorDef op_def = CreateOperatorDef(
      "IncrementByOne", "", vector<string>{"X"},
      vector<string>{"X"});
  Workspace ws;
  TensorCPU source_tensor(vector<TIndex>{2, 3});
  for (int i = 0; i < 6; ++i) {
    source_tensor.mutable_data<float>()[i] = i;
  }
  ws.CreateBlob("X")->GetMutable<TensorCPU>()->CopyFrom(source_tensor);
  unique_ptr<OperatorBase> op(CreateOperator(op_def, &ws));
  EXPECT_TRUE(op.get() != nullptr);
  EXPECT_TRUE(op->Run());
  const TensorCPU& output = ws.GetBlob("X")->Get<TensorCPU>();
  EXPECT_EQ(output.ndim(), 2);
  EXPECT_EQ(output.dim(0), 2);
  EXPECT_EQ(output.dim(1), 3);
  for (int i = 0; i < 6; ++i) {
    EXPECT_EQ(output.data<float>()[i], i + 1);
  }
}

TEST(OperatorFallbackTest, GPUIncrementByOneOp) {
  if (!HasCudaGPU()) return;
  OperatorDef op_def = CreateOperatorDef(
      "IncrementByOne", "", vector<string>{"X"},
      vector<string>{"X"});
  op_def.mutable_device_option()->set_device_type(CUDA);
  Workspace ws;
  TensorCPU source_tensor(vector<TIndex>{2, 3});
  for (int i = 0; i < 6; ++i) {
    source_tensor.mutable_data<float>()[i] = i;
  }
  ws.CreateBlob("X")->GetMutable<TensorCUDA>()->CopyFrom(source_tensor);
  unique_ptr<OperatorBase> op(CreateOperator(op_def, &ws));
  EXPECT_TRUE(op.get() != nullptr);
  EXPECT_TRUE(op->Run());
  const TensorCUDA& output = ws.GetBlob("X")->Get<TensorCUDA>();
  TensorCPU output_cpu(output);
  EXPECT_EQ(output.ndim(), 2);
  EXPECT_EQ(output.dim(0), 2);
  EXPECT_EQ(output.dim(1), 3);
  for (int i = 0; i < 6; ++i) {
    EXPECT_EQ(output_cpu.data<float>()[i], i + 1);
  }
}

}  // namespace caffe2
