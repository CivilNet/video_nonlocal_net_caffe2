# Copyright 2014 Nervana Systems Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os.path
import re
import traceback as tb
import numpy as np
from pycuda.compiler import SourceModule
from pycuda.tools import context_dependent_memoize
from pytools import memoize, memoize_method
import pycuda.driver as drv
import nervanagpu as ng
# from ipdb import set_trace

_ew_template = r"""

#include <float.h>

%(common)s

#define THREADS %(threads)s

__global__ void %(name)s (
    unsigned* rand_state,
    %(arguments)s)
{
    const int tid  = threadIdx.x;
    const int bid  = blockIdx.x;

    extern __shared__ float sPartials[];

    %(inits)s
"""

_stage_template = {
    "loop" : r"""

    for (int i = tid; i < n{0}; i += THREADS)
    {{
        %(loads{0})s

        %(ops{0})s
    }}
""",

    "red32" :r"""

    #pragma unroll
    for (int i = 16; i > 0; i >>= 1)
    {{
        %(shfl_red{0})s
    }}

""",

    "red" :r"""

    sPartials[tid] = %(var_red{0})s;
    __syncthreads();

    #pragma unroll
    for (int a = THREADS >> 1; a > 32; a >>= 1)
    {{
        if ( tid < a )
            %(share1_red{0})s
        __syncthreads();
    }}

    if ( tid < 32 )
    {{
        %(share2_red{0})s

        #pragma unroll
        for (int i = 16; i > 0; i >>= 1)
            %(shfl_red{0})s

        sPartials[tid] = %(var_red{0})s;
    }}
    __syncthreads();
    %(var_red{0})s = sPartials[0];
""",

    "red_ops" :r"""

        %(ops{0})s
""",

    "red_out" : r"""

    if ( tid == 0 )
    {{
        %(ops{0})s
    }}
"""
}

_fin_template = r"""
    %(finish)s
}
"""

_init_rand_func = r"""
    unsigned lfsr0, lfsr1, lfsr2;
    unsigned idx = bid * THREADS + tid;
    rand_state += idx % (2048*32);
    lfsr0 = *rand_state;
    asm("mov.b32 %0, %%clock;"          : "=r"(lfsr1) :);
    asm("mov.b32 %0, %%globaltimer_lo;" : "=r"(lfsr2) :);
    asm("shf.r.clamp.b32 %0,%0,%0,%1;"  : "=r"(lfsr1) : "r"((lfsr1 & 31)^tid));
    asm("shf.r.clamp.b32 %0,%0,%0,%1;"  : "=r"(lfsr2) : "r"((lfsr2 & 31)^tid));
    lfsr1 ^= (idx << 3)  ^ (idx << 7);
    lfsr2 ^= (idx << 17) ^ (idx << 23);
"""

_init_rand_round_func = r"""
    int i_rand_scale = (127 - 32 - mantissa_bits) << 23;
    float rand_scale = *(float*)&i_rand_scale;
    unsigned rand_mask = 0xffffffff << (23 - mantissa_bits);
"""

_finish_rand_func = r"""
    *rand_state = lfsr0 ^ lfsr1 ^ lfsr2;
"""

_common_urand_gen = r"""
__device__ unsigned urand_gen(unsigned& lfsr0, unsigned& lfsr1, unsigned& lfsr2)
{
    lfsr0 = ((lfsr0 & 0xfffffffe) << 12) ^ (((lfsr0 << 13) ^ lfsr0) >> 19);
    lfsr1 = ((lfsr1 & 0xfffffff8) <<  4) ^ (((lfsr1 << 2)  ^ lfsr1) >> 25);
    lfsr2 = ((lfsr2 & 0xfffffff0) << 11) ^ (((lfsr2 << 3)  ^ lfsr2) >> 11);
    return lfsr0 ^ lfsr1 ^ lfsr2;
}
"""

_common_frand = r"""
__device__ __forceinline__ float frand(unsigned& lfsr0, unsigned& lfsr1, unsigned& lfsr2)
{
    unsigned urand = urand_gen(lfsr0, lfsr1, lfsr2);
    float val;
    asm("cvt.rn.f32.u32 %0, %1;\n\t"
        "mul.f32 %0, %0, 0F2f800000;"
        : "=f"(val) : "r"(urand));
    return val;
}
"""

_common_round = {

    "random" : {

        "f4" : r"""
__device__ float fp32_to_fp32_rand(
    float val, unsigned& lfsr0, unsigned& lfsr1, unsigned& lfsr2, float rand_scale, unsigned rand_mask)
{
    unsigned urand = urand_gen(lfsr0, lfsr1, lfsr2);

    float ret;
    asm("{\n\t"
        ".reg .f32 exponent, frand, result;\n\t"
        "and.b32 exponent, %1, 0xff800000;\n\t"
        "mul.f32 exponent, exponent, %2;\n\t"
        "cvt.rz.f32.u32 frand, %3;\n\t"
        "fma.rz.f32 result, exponent, frand, %1;\n\t"
        "and.b32 %0, result, %4;\n\t"
        "}" : "=f"(ret) : "f"(val), "f"(rand_scale), "r"(urand), "r"(rand_mask));

    return ret;
}
""",
        "f2" : r"""
__device__ unsigned short fp32_to_fp16_rand(
    float val, unsigned& lfsr0, unsigned& lfsr1, unsigned& lfsr2, float rand_scale, unsigned rand_mask)
{
    unsigned urand = urand_gen(lfsr0, lfsr1, lfsr2);

    unsigned short half;
    asm("{\n\t"
        ".reg .f16 result16;\n\t"
        ".reg .f32 exponent, frand, result32;\n\t"
        "and.b32 exponent, %1, 0xff800000;\n\t"
        "mul.f32 exponent, exponent, %2;\n\t"
        "cvt.rz.f32.u32 frand, %3;\n\t"
        "fma.rz.f32 result32, exponent, frand, %1;\n\t"
        "and.b32 result32, result32, %4;\n\t"
        "cvt.rz.f16.f32 result16, result32;\n\t"
        "mov.b16 %0, result16;\n\t"
        "}" : "=h"(half) : "f"(val), "f"(rand_scale), "r"(urand), "r"(rand_mask));

    return half;
}
""",
        "i4"  : r"""
__device__ __forceinline__ int fp32_to_int32_rand(
    float val, unsigned& lfsr0, unsigned& lfsr1, unsigned& lfsr2)
{
    unsigned urand = urand_gen(lfsr0, lfsr1, lfsr2);
    int ret;
    asm("{\n\t"
        ".reg .f32 frand, result32;\n\t"
        "cvt.rz.f32.u32 frand, %2;\n\t"
        "copysign.f32 frand, %1, frand;\n\t"
        "mul.rz.f32 frand, frand, 0F2f800000;\n\t"
        "add.rz.f32 result32, frand, %1;\n\t"
        "cvt.rzi.s32.f32 %0, result32;\n\t"
        "}" : "=r"(ret) : "f"(val), "r"(urand));
    return ret;
}
""",
        "i2"  : r"""
__device__ __forceinline__ short fp32_to_int16_rand(
    float val, unsigned& lfsr0, unsigned& lfsr1, unsigned& lfsr2)
{
    unsigned urand = urand_gen(lfsr0, lfsr1, lfsr2);
    short half;
    asm("{\n\t"
        ".reg .f32 frand, result32;\n\t"
        "cvt.rz.f32.u32 frand, %2;\n\t"
        "copysign.f32 frand, %1, frand;\n\t"
        "mul.rz.f32 frand, frand, 0F2f800000;\n\t"
        "add.rz.f32 result32, frand, %1;\n\t"
        "cvt.rzi.s16.f32 %0, result32;\n\t"
        "}" : "=h"(half) : "f"(val), "r"(urand));
    return half;
}
""",
        "i1"  : r"""
__device__ __forceinline__ char fp32_to_int8_rand(
    float val, unsigned& lfsr0, unsigned& lfsr1, unsigned& lfsr2)
{
    unsigned urand = urand_gen(lfsr0, lfsr1, lfsr2);
    int ret;
    asm("{\n\t"
        ".reg .f32 frand, result32;\n\t"
        "cvt.rz.f32.u32 frand, %2;\n\t"
        "copysign.f32 frand, %1, frand;\n\t"
        "mul.rz.f32 frand, frand, 0F2f800000;\n\t"
        "add.rz.f32 result32, frand, %1;\n\t"
        "cvt.rzi.s8.f32 %0, result32;\n\t"
        "}" : "=r"(ret) : "f"(val), "r"(urand));
    return ret;
}
""",
    },
    "nearest" : {

        "f2" : r"""
__device__ __forceinline__ unsigned short fp32_to_fp16(float val)
{
    unsigned short ret;
    asm("{\n\t"
        ".reg .f16 f16;\n\t"
        "cvt.rn.f16.f32 f16, %1;"
        "mov.b16 %0, f16;\n\t"
        "}" : "=h"(ret) : "f"(val));
    return ret;
}
""",
        "i4" : r"""
__device__ __forceinline__ int fp32_to_int32(float val)
{
    int ret;
    asm("cvt.rni.s32.f32 %0, %1;" : "=r"(ret) : "f"(val));
    return ret;
}
""",
        "u4" : r"""
__device__ __forceinline__ unsigned fp32_to_uint32(float val)
{
    unsigned ret;
    asm("cvt.rni.u32.f32 %0, %1;" : "=r"(ret) : "f"(val));
    return ret;
}
""",
        "i2" : r"""
__device__ __forceinline__ short fp32_to_int16(float val)
{
    short ret;
    asm("cvt.rni.s16.f32 %0, %1;" : "=h"(ret) : "f"(val));
    return ret;
}
""",
        "u2" : r"""
__device__ __forceinline__ unsigned short fp32_to_uint16(float val)
{
    unsigned short;
    asm("cvt.rni.u16.f32 %0, %1;" : "=h"(ret) : "f"(val));
    return ret;
}
""",
        "i1" : r"""
__device__ __forceinline__ char fp32_to_int8(float val)
{
    int ret;
    asm("cvt.rni.s8.f32 %0, %1;" : "=r"(ret) : "f"(val));
    return ret;
}
""",
        "u1" : r"""
__device__ __forceinline__ unsigned char fp32_to_uint8(float val)
{
    unsigned ret;
    asm("cvt.rni.u8.f32 %0, %1;" : "=r"(ret) : "f"(val));
    return ret;
}
""",
    },
}
# random rounding not yet used for these types
for dtype in ("u4","u2","u1"):
    _common_round["random"][dtype] = _common_round["nearest"][dtype]


_common_fp16_to_fp32 = r"""
__device__ __forceinline__ float fp16_to_fp32(unsigned short val)
{
    float ret;
    asm("{\n\t"
        ".reg .f16 f16;\n\t"
        "mov.b16 f16, %1;\n\t"
        "cvt.f32.f16 %0, f16;"
        "}" : "=f"(ret) : "h"(val));
    return ret;
}
"""

_ew_types = {
    "f4" : {
        "type" : "float",
        "cvt"  : "",
    },
    "f2" : {
        "type" : "unsigned short",
        "cvt"  : "fp16_to_fp32",
    },
    "i4" : {
        "type" : "int",
        "cvt"  : "(float)",
    },
    "u4" : {
        "type" : "unsigned int",
        "cvt"  : "(float)",
    },
    "i2" : {
        "type" : "short",
        "cvt"  : "(float)",
    },
    "u2" : {
        "type" : "unsigned short",
        "cvt"  : "(float)",
    },
    "i1" : {
        "type" : "char",
        "cvt"  : "(float)",
    },
    "u1" : {
        "type" : "unsigned char",
        "cvt"  : "(float)",
    },
}

_ew_strings = {

    # 0: arg_id, 1: stage, 2: type, 3: cvt
    "in0" : {
        "arguments" : "const {2}* a{0}_in, int row_strd{0}, int col_strd{0}",
        "inits"     : "const {2}* a{0}_in{1} = a{0}_in + bid * row_strd{0} + tid * col_strd{0};\n"
                  "    int a{0}_inc{1} = THREADS * col_strd{0};",
        "loads"     : "float a{0} = {3}(__ldg(a{0}_in{1}));\n"
              "        a{0}_in{1} += a{0}_inc{1};",
    },
    "in1" : {
        "arguments" : "const {2}* a{0}_in, int row_strd{0}, int col_strd{0}, const int* take{0}_in",
        "inits"     : "const {2}* a{0}_in{1} = a{0}_in + __ldg(take{0}_in + bid) * row_strd{0} + tid * col_strd{0};\n"
                  "    int a{0}_inc{1} = THREADS * col_strd{0};",
        "loads"     : "float a{0} = {3}(__ldg(a{0}_in{1}));\n"
              "        a{0}_in{1} += a{0}_inc{1};",
    },
    "in2" : {
        "arguments" : "const {2}* a{0}_in, int row_strd{0}, int col_strd{0}, const int* take{0}_in",
        "inits"     : "const {2}* a{0}_in{1} = a{0}_in + bid * row_strd{0};\n"
                  "    const int* take{0}_in{1} = take{0}_in + tid;",
        "loads"     : "float a{0} = {3}(__ldg(a{0}_in{1} + __ldg(take{0}_in{1})));\n"
              "        take{0}_in{1} += THREADS;",
    },
    "out0" : {
        "arguments" : "{2}* a_out, int row_strd, int col_strd",
        "inits"     : "a_out += bid * row_strd + tid * col_strd;\n"
                  "    int out_inc = THREADS * col_strd;",
        "output"    : "*a_out = {0};\n        a_out += out_inc;",
    },
    "out1" : {
        "arguments" : "{2}* a_out, int row_strd, int col_strd, const int* take_out",
        "inits"     : "a_out += __ldg(take_out + bid) * row_strd + tid * col_strd;\n"
                  "    int out_inc = THREADS * col_strd;",
        "output"    : "*a_out = {0};\n        a_out += out_inc;",

    },
    "out2" : {
        "arguments" : "{2}* a_out, int row_strd, int col_strd, const int* take_out",
        "inits"     : "a_out += bid * row_strd;\n"
                  "    take_out += tid;",

        "output"    : "*(a_out + __ldg(take_out)) = {0};\n        take_out += THREADS;",

    },
    "onehot0" : {
        "arguments" : "const int* onehot{0}_in",
        "inits"     : "onehot{0}_in += tid;",
        "loads"     : "int onehot{0} = __ldg(onehot{0}_in);\n"
              "        onehot{0}_in += THREADS;",
    },
    "onehot1" : {
        "arguments" : "const int* onehot{0}_in",
        "inits"     : "int onehot{0} = __ldg(onehot{0}_in + bid);\n",
        "loads"     : "",
    },
    "const" : {
        "arguments" : "float c{0}",
    },
    "round" : {
        "random"  : {
            "f4"  : "float {0}          = fp32_to_fp32_rand({1}, lfsr0, lfsr1, lfsr2, rand_scale, rand_mask);",
            "f2"  : "unsigned short {0} = fp32_to_fp16_rand({1}, lfsr0, lfsr1, lfsr2, rand_scale, rand_mask);",
            "u4"  : "unsigned int {0}   = fp32_to_uint32({1});",
            "u2"  : "unsigned short {0} = fp32_to_uint16({1});",
            "u1"  : "unsigned char {0}  = fp32_to_uint8({1});",
            "i4"  : "int {0}            = fp32_to_int32_rand({1});",
            "i2"  : "short {0}          = fp32_to_int16_rand({1});",
            "i1"  : "char {0}           = fp32_to_int8_rand({1});",
        },
        "nearest" : {
            "f2"  : "unsigned short {0} = fp32_to_fp16({1});",
            "u4"  : "unsignedint {0}    = fp32_to_uint32({1});",
            "u2"  : "unsigned short {0} = fp32_to_uint16({1});",
            "u1"  : "unsigned char {0}  = fp32_to_uint8({1});",
            "i4"  : "int {0}            = fp32_to_int32({1});",
            "i2"  : "short {0}          = fp32_to_int16({1});",
            "i1"  : "char {0}           = fp32_to_int8({1});",
        },
    },
}

_is_finite = r"""
float {0};
asm("{{\n\t"
    ".reg .pred is_finite;\n\t"
    "testp.finite.f32 is_finite, %1;\n\t"
    "selp.f32 %0, 0F3f800000, 0F00000000, is_finite;\n\t"
    "}}" : "=f"({0}) : "f"({1}));
"""

# Note: binary operands come off the stack in reverse order
_float_ops = {
    "assign"  : (2, "unused" ),
    "add"     : (2, 'float {0} = {2} + {1};' ),
    "sub"     : (2, 'float {0} = {2} - {1};' ),
    "mul"     : (2, 'float {0} = {2} * {1};' ),
    "div"     : (2, 'float {0} = {2} / {1};' ),
    "eq"      : (2, "float {0} = {2} == {1};"   ),
    "ne"      : (2, "float {0} = {2} != {1};"   ),
    "lt"      : (2, "float {0} = {2} <  {1};"   ),
    "le"      : (2, "float {0} = {2} <= {1};"   ),
    "gt"      : (2, "float {0} = {2} >  {1};"   ),
    "ge"      : (2, "float {0} = {2} >= {1};"   ),
    "minimum" : (2, "float {0} = fminf({2},{1});" ),
    "maximum" : (2, "float {0} = fmaxf({2},{1});" ),
    "pow"     : (2, "float {0} = powf({2},{1});"  ),
    "finite"  : (1, _is_finite ),
    "neg"     : (1, "float {0} = -{1};"         ),
    "abs"     : (1, "float {0} = abs({1});"     ),
    "sgn"     : (1, "float {0} = copysignf(1.0f, {1});"     ),
    "sqrt"    : (1, "float {0} = sqrtf({1});"   ),
    "sqr"     : (1, "float {0} = {1} * {1};"    ),
    "exp"     : (1, "float {0} = expf({1});"    ),
    "log"     : (1, "float {0} = logf({1});"    ),
    "exp2"    : (1, "float {0} = exp2f({1});"   ),
    "log2"    : (1, "float {0} = log2f({1});"   ),
    "sig"     : (1, "float {0} = 1.0f/(1.0f + expf(-{1}));"),
    "sig2"    : (1, "float {0} = 1.0f/(1.0f + exp2f(-{1}));"),
    "tanh"    : (1, "float {0} = tanhf({1});"   ),
    "tanh2"   : (1, "float {0} = (exp2f(2.0f*{1}) - 1.0f) / (exp2f(2.0f*{1}) + 1.0f);" ),
    "rand"    : (0, "float {0} = frand(lfsr0, lfsr1, lfsr2);"),
    "onehot"  : (0, "float {0} = {1} == {2};"),
}

_reduction_ops = {
    "sum" : {
        "inits"      : "float {0} = 0.0f;",
        "ops"        : "{0} += {1};",
        "shfl_red"   : "{0} += __shfl_xor({0}, i);",
        "share1_red" : "sPartials[tid] += sPartials[tid + a];",
        "share2_red" : "{0} = sPartials[tid] + sPartials[tid + 32];",
    },
    "max" : {
        "inits"      : "float {0} = -FLT_MAX;",
        "ops"        : "{0} = fmaxf({0}, {1});",
        "shfl_red"   : "{0} = fmaxf({0}, __shfl_xor({0}, i));",
        "share1_red" : "sPartials[tid] = fmaxf(sPartials[tid], sPartials[tid + a]);",
        "share2_red" : "{0} = fmaxf(sPartials[tid], sPartials[tid + 32]);",
    },
    "min" : {
        "inits"      : "float {0} = FLT_MAX;",
        "ops"        : "{0} = fminf({0}, {1});",
        "shfl_red"   : "{0} = fminf({0}, __shfl_xor({0}, i));",
        "share1_red" : "sPartials[tid] = fminf(sPartials[tid], sPartials[tid + a]);",
        "share2_red" : "{0} = fminf(sPartials[tid], sPartials[tid + 32]);",
    },
    "argmax" : {
        "inits"     : "int {0} = -1; float max = -FLT_MAX;",
        "ops"       : "if ({1} > max) {{ max = {1}; {0} = i; }}",
        "shfl_red"  : "float max2 = __shfl_xor(max, i); int argMax2 = __shfl_xor({0}, i);\n"
              "        if (max2 > max) {{ max = max2; {0} = argMax2; }}"
              "        else if (max2 == max && argMax2 < {0}) {{ {0} = argMax2; }}",
    },
    "argmin" : {
        "inits"     : "int {0} = -1; float min = FLT_MAX;",
        "ops"       : "if ({1} < min) {{ min = {1}; {0} = i; }}",
        "shfl_red"  : "float min2 = __shfl_xor(min, i); int argMin2 = __shfl_xor({0}, i);\n"
              "        if (min2 < min) {{ min = min2; {0} = argMin2; }}"
              "        else if (min2 == min && argMin2 < {0}) {{ {0} = argMin2; }}",
    },
}


# rebuild a mutable tree from the stack
# flag each op node with whether it is scalar or not
# also include a count of reductions under this node:
# node: [ arg(op, tensor or const), is_scalar, red_count, left_child, right_child ]
def _build_tree(type_args):

    stack = list()
    for arg in type_args:
        arg_type = arg[0]
        if arg_type in _float_ops:
            numops = _float_ops[arg_type][0]
            # ops with zero args default to non-scalar
            node = [arg, numops > 0, 0]
            for i in range(numops):
                operand = stack.pop()
                # if child is another node in the tree:
                if type(operand) is list:
                    # accumulate reduction count
                    node[2] += operand[2]
                    # if a child is not scalar, then neither is this node
                    if operand[1] == 0:
                        node[1] = False
                # if child is an input tensor (an output tensor has id=0) then this node is not scalar
                elif operand[0] is ng.GPUTensor and operand[1] > 0:
                    node[1] = False
                # children start at position 3 and are added in reverse order
                node.insert(3, operand)
            stack.append(node)

        elif arg_type in _reduction_ops:
            operand = stack.pop()
            reds    = 1
            # if child is another node accumulate reduction count
            if type(operand) is list:
                reds += operand[2]
            # reductions are scalar by definition
            stack.append([arg, True, reds, operand])

        else:
            # tensors and scalars just get added to the stack
            # for later processing with operators
            stack.append(arg)

    # the stack should now contain just a single node which is the complete tree
    return stack[0]

# for debugging
def _print_tree(node, level=0):

    if type(node) is list:
        print ("    " * level) + ", ".join(str(s) for s in node[0:3])
        if len(node) > 3:
            _print_tree(node[3], level+1)
        if len(node) > 4:
            _print_tree(node[4], level+1)
    else:
        print ("    " * level) + str(node)

# generate a stack from a portion of the tree
def _post_order(node, stack=None):

    if stack is None:
        stack = list()

    if type(node) is list:
        if len(node) > 3:
            _post_order(node[3], stack)
        if len(node) > 4:
            _post_order(node[4], stack)
        stack.append(node[0])
    else:
        stack.append(node)
    return stack

# Takes a node from the tree and searchs for any previously processed duplicates.
# If not a duplicate, returns a stage based from that node.
# If a duplicate, the node is replaced with an alias to the dup stage.
# In both cases the tree is removed below this node (and the alias remains).
def _process_node(node, aliases, duplicates):

    # generate a unique key from the stack of everything below this reduction
    stack = _post_order(node)
    key   = list()
    for item in stack:
        # for operations, just append the name
        # aliases require the id as well since they encapsulate specific tensors and constants
        if type(item[0]) is str and item not in aliases:
            key.append(item[0])
        # For tensor or constant or alias, append type and id.
        else:
            key.append(item[0:2])
    key = tuple(key)

    # use the generated key to look for duplicates
    dup_node = duplicates.get(key, False)
    if dup_node:
        # if this is a duplicate, replace the stack with the op node of the original reduction
        node[0] = dup_node
        # no new stage is returned in this case, the node is just converted into an alias
        stack = None
    else:
        # first time seeing this reduction, record it in the dict
        # the last item in the stack will be the reduction op
        duplicates[key] = stack[-1]
        # record which nodes can be aliased
        aliases.add(stack[-1])

    # drop any children (children start at position 3)
    while len(node) > 3:
        node.pop()

    return stack

# Split out all reductions and post reduction scalar operations into seperate stacks (stages)
# This leaves remaining in the tree anything not in these categories.
def _split_stages(node, duplicates=None, aliases=None, stages=None, parents=None):

    # init data structures
    if duplicates is None:
        duplicates = dict()
        aliases    = set()
        stages     = list()
        parents    = list()

    if type(node) is list:

        # don't count assignment node as a parent,
        # it will always exist in the final stage which is processed outside of this function
        if node[0][0] != "assign":
            parents.append(node)

        # post order traversal (pulls the stages deepest in the tree first)
        if len(node) > 3:
            _split_stages(node[3], duplicates, aliases, stages, parents)
        if len(node) > 4:
            _split_stages(node[4], duplicates, aliases, stages, parents)

        if len(parents) > 0:
            parents.pop()

        if node[0][0] in _reduction_ops:

            red_stack = _process_node(node, aliases, duplicates)
            if red_stack:
                # add this reduction stack to the stages
                stages.append(("reduction",red_stack))

            # decrement reduction count for all parents
            for parent in parents:
                parent[2] -= 1

            # walk up the parent list
            # TODO: potentially do this iteratively to find longest common set of operations
            scalar_parent = None
            for parent in parents[::-1]:
                # find the highest parent that is both scalar and has no other child reductions
                if parent[1] and parent[2] == 0:
                    scalar_parent = parent
                else:
                    break

            # if there are any scalar operations over this reduction, remove them from the tree as well
            if scalar_parent is not None:

                scalar_stack = _process_node(scalar_parent, aliases, duplicates)
                if scalar_stack:
                    # add this scalar stack to the stages
                    stages.append(("scalar",scalar_stack))

    return stages

def _init_rand(template_vals):

    template_vals["common"].append(_common_urand_gen)
    template_vals["inits"].append(_init_rand_func)
    template_vals["finish"].append(_finish_rand_func)
    return True

@context_dependent_memoize
def _get_compound_kernel(type_args):

    # from the stack, rebuild a mutable tree
    tree = _build_tree(type_args)
    # _print_tree(tree)
    #exit()

    # split all reductions and post reduction scalar operations out of the tree
    # sub-trees are converted to stacks and pushed onto stages list
    stages = _split_stages(tree)
    # _print_tree(tree)
    # exit()

    # set the final stage type to type of output (scalar or elementwise)
    last_stage = "red_out" if tree[1] == 1 else "ew_out"
    # convert the remainder of tree to stack
    stages.append((last_stage, _post_order(tree)))

    # for stage, stage_data in enumerate(stages):
    #     print stage_data[0], stage
    #     for s in stage_data[1]: print s
    #     print
    # exit()

    stack         = list()
    placeholders  = list()
    stage_out_reg = dict()
    arg_dict      = dict()
    array_ids     = set()
    fp16In        = False
    rand_init     = False
    rand_func     = False
    threads       = type_args[-1][3]
    template      = _ew_template
    template_vals = {
        "threads" : threads,
        "name"    : _get_kernel_name(),
        "common"  : list(),
        "inits"   : list(),
        "finish"  : list(),
    }

    for stage, stage_data in enumerate(stages):

        stage_type, stage_stack = stage_data
        new_placeholders = list()

        # build out the template as we process stages
        if stage_type == "reduction":

            new_placeholders.append("loads%d"    % stage)
            new_placeholders.append("ops%d"      % stage)
            new_placeholders.append("shfl_red%d" % stage)
            template += _stage_template["loop"].format(stage)
            if threads > 32:
                new_placeholders.append("var_red%d"    % stage)
                new_placeholders.append("share1_red%d" % stage)
                new_placeholders.append("share2_red%d" % stage)
                template += _stage_template["red"].format(stage)
            else:
                template += _stage_template["red32"].format(stage)

        elif stage_type == "scalar":

            new_placeholders.append("ops%d" % stage)
            template += _stage_template["red_ops"].format(stage)

        elif stage_type == "red_out":

            new_placeholders.append("ops%d" % stage)
            template += _stage_template["red_out"].format(stage)

        else: # ew_out

            new_placeholders.append("loads%d" % stage)
            new_placeholders.append("ops%d"   % stage)
            template += _stage_template["loop"].format(stage)

        for key in new_placeholders:
            template_vals[key] = []
        placeholders.extend(new_placeholders)

        for arg_i, arg in enumerate(stage_stack):

            arg_type, arg_id = arg[0:2]

            # Array operands
            if arg_type is ng.GPUTensor:

                dtype, take_axis = arg[2:4]

                is_out_tensor = True if stage == len(stages)-1 and arg_i == 0 else False

                # first arg is output array, don't put on stack
                if is_out_tensor:
                    out_dtype = dtype
                    out_take  = take_axis
                else:
                    stack.append("a%d" % arg_id)

                # 0: arg_id, 1: stage, 2: type, 3: cvt
                ew_dtype = _ew_types[dtype]
                fmt = (arg_id, stage, ew_dtype["type"], ew_dtype["cvt"])

                # First time we see a tensor initialize everything
                if arg_id not in array_ids:

                    array_ids.add(arg_id)
                    array_ids.add((arg_id, stage))

                    sig = "Pii"
                    if take_axis > 0:
                        sig += "P"

                    # output tensor
                    if is_out_tensor:
                        ew_out    = _ew_strings["out%d" % take_axis]
                        arguments = ew_out["arguments"].format(*fmt)
                        template_vals["inits"].append(ew_out["inits"].format(*fmt))
                    # input tensors
                    else:
                        ew_in     = _ew_strings["in%d" % take_axis]
                        loads     = "loads%d" % stage
                        arguments = ew_in["arguments"].format(*fmt)
                        template_vals["inits"].append(ew_in["inits"].format(*fmt))
                        template_vals[ loads ].append(ew_in["loads"].format(*fmt))

                    if dtype == 'f2' and not fp16In:
                        template_vals["common"].append(_common_fp16_to_fp32)
                        fp16In = True

                    arg_dict[arg] = (sig, arguments)

                # Subsequent times we see a tensor just initialize inits and loads
                elif (arg_id, stage) not in array_ids:
                    array_ids.add((arg_id, stage))
                    ew_in = _ew_strings["in%d" % take_axis]
                    loads = "loads%d" % stage
                    template_vals["inits"].append(ew_in["inits"].format(*fmt))
                    template_vals[loads  ].append(ew_in["loads"].format(*fmt))

            # Constant operands
            elif arg_type is float:

                stack.append("c%d" % arg_id)
                if arg not in arg_dict:
                    arg_dict[arg] = ("f", _ew_strings["const"]["arguments"].format(arg_id))

            # Operations (arg_type = op_name)
            else:

                if arg_type == "assign":

                    ops = "ops%d" % stage

                    # loop end condition for last stage
                    sig = "i"
                    arguments = ["const int n%d" % stage]

                    # rounding mode
                    if arg[2]:
                        mode = "random"
                        sig += "i"
                        arguments.append("const int mantissa_bits")
                        if not rand_init:
                            rand_init = _init_rand(template_vals)
                        template_vals["inits"].append(_init_rand_round_func)
                    else:
                        mode = "nearest"

                    arg_dict[arg] = (sig, ", ".join(arguments))

                    out_val = stack.pop()
                    # if the last stack value came from an argmax/min just do implicit type conversion
                    if out_val[0] == "i" and out_dtype[0] in "iu":
                        ew_round = None
                    else:
                        ew_round  = _ew_strings["round"][mode].get(out_dtype, None)
                        ew_common = _common_round[mode].get(out_dtype, None)
                        if ew_common:
                            template_vals["common"].append(ew_common)

                    if ew_round:
                        round_val = "r%d" % arg_id
                        template_vals[ops].append(ew_round.format(round_val, out_val))
                    else:
                        round_val = out_val

                    template_vals[ops].append(_ew_strings["out%d" % out_take]["output"].format(round_val))

                elif arg in stage_out_reg:

                    stack.append(stage_out_reg[arg])

                elif arg_type in _float_ops:

                    if len(template_vals["name"]) < 16:
                        template_vals["name"].append(arg_type)

                    ops = "ops%d" % stage

                    (num_ops, op_code) = _float_ops[arg_type]

                    if arg_type == "rand":
                        if not rand_init:
                            rand_init = _init_rand(template_vals)
                        if not rand_func:
                            template_vals["common"].append(_common_frand)
                            rand_func = True

                    op_list = [ "r%d" % arg_id ]

                    #build the operands from the stack
                    for i in range(num_ops):
                        op_list.append(stack.pop())

                    if arg_type == "onehot":

                        hot_axis = arg[2]
                        test_val = "i" if hot_axis else "bid"

                        ew_in = _ew_strings[arg_type + str(hot_axis)]
                        loads = "loads%d" % stage
                        template_vals["inits"].append(ew_in["inits"].format(arg_id))
                        template_vals[ loads ].append(ew_in["loads"].format(arg_id))
                        op_list.append("onehot%d" % arg_id)
                        op_list.append(test_val)

                        arg_dict[arg] = ("P", ew_in["arguments"].format(arg_id))

                    template_vals[ops].append(op_code.format(*op_list))

                    # if this is the last op on the current stack, store its register stage
                    # in the stage output dict
                    if arg_i == len(stage_stack)-1:
                        stage_out_reg[arg] = op_list[0]
                    # otherwise push the reg onto the stack as normal
                    else:
                        stack.append(op_list[0])

                elif arg_type in _reduction_ops:

                    if len(template_vals["name"]) < 16:
                        template_vals["name"].append(arg_type)

                    # loop end condition for current stage
                    # add regardless of duplicate reduction stage
                    arg_dict[arg] = ("i", "const int n%d" % stage)

                    # avoid float conversion for argmax/min
                    reg = "i" if "arg" == arg_type[0:3] else "r"

                    ops         = "ops%d"      % stage
                    shfl_red    = "shfl_red%d" % stage
                    red_arg     = "%s%d"       % (reg, arg_id)
                    red_strings = _reduction_ops[arg_type]
                    stack_arg   = stack.pop()

                    template_vals["inits" ].append(red_strings["inits"   ].format(red_arg))
                    template_vals[ops     ].append(red_strings["ops"     ].format(red_arg, stack_arg))
                    template_vals[shfl_red].append(red_strings["shfl_red"].format(red_arg))
                    if threads > 32:
                        var_red  = "var_red%d"    % stage
                        shr1_red = "share1_red%d" % stage
                        shr2_red = "share2_red%d" % stage
                        template_vals[var_red ].append(red_arg)
                        template_vals[shr1_red].append(red_strings["share1_red"].format(red_arg))
                        template_vals[shr2_red].append(red_strings["share2_red"].format(red_arg))

                    # reduction ops are always the last on the stack
                    # just store the register state in the stage output dict
                    stage_out_reg[arg] = red_arg

                else:
                    raise ValueError("Bad op type.")

    template += _fin_template

    # since we reorderd the operations we need to generate the argument list in the original order
    sig       = "P"
    arguments = list()
    unused    = 1
    for arg in type_args:
        params = arg_dict.get(arg, False)
        if params:
            sig += params[0]
            arguments.append(params[1])
            del arg_dict[arg]
        # fill in the loop counter for the duplicate reductions that were removed
        elif arg[0] in _reduction_ops:
            sig += "i"
            arguments.append("const int unused%d" % unused)
            unused += 1

    # convert lists to strings
    template_vals["name"]       = "_".join(template_vals["name"])
    template_vals["common"]     = "\n".join(template_vals["common"])
    template_vals["arguments"]  = ",\n    ".join(arguments)
    template_vals["inits"]      = "\n    ".join(template_vals["inits"])
    template_vals["finish"]     = "\n".join(template_vals["finish"])

    # add the dynamic placeholders: loads#, ops#, reduction#
    for key in placeholders:
        template_vals[key]      = "\n        ".join(template_vals[key])

    # populate the template
    code = template % template_vals

    # debugging:
    # print "Compiling %s" % template_vals["name"]
    # f = open("%s.cu" % template_vals["name"], "w")
    # f = open("kernel.cu", "w")
    # print >>f, code
    # f.close()

    module = SourceModule(code, options=[ "--use_fast_math" ]) # ,"-G" , keep=False
    kernel = module.get_function(template_vals["name"])
    kernel.name = template_vals["name"]
    kernel.prepare(sig)

    return kernel

@memoize
def _get_fast_ew_dims(size):

    # TODO: I can probably do much better than this code below,
    # but I think most tensors are evenly divisable by 256 off the bat.
    ew_size = 256
    while ew_size > 0:
        if size % ew_size == 0:
            break
        ew_size -= 32
    if ew_size == 0:
        ew_size = 255
        while ew_size > 0:
            if size % ew_size == 0:
                break
            ew_size -= 1

    shape = (size // ew_size, ew_size)
    return (shape, ng._contiguous_strides(shape))

# TODO: build a program wide DAG and only call this once at startup per assignment.
def call_compound_kernel(rand_state, *args):
    """
    Pass in a list of GPUTensor objects, constants and operators in postfix notation..

    C +=  2.5 * A * B + 1
    call_compound_ew_kernel(C, 2.5, A, "mul", B, "mul", 1, "add", C, "add", "assign")
    """
    out         = None
    arg_cnt     = 0
    op_cnt      = 0
    array_ids   = {}
    const_ids   = {}
    kernel_args = [ rand_state, ]
    type_args   = []
    shape_stack = []
    threads     = 32
    red_depth   = 0
    # Apply reduction constraints and determine thread axis
    # Blocks will be allocated counter to this axis
    # Also detect if this is a broadcast or transpose op.
    contiguous = True
    reduction  = False
    broadcast  = False
    transpose  = False
    argminmax  = False
    takeop     = False
    axis = 1
    for arg in args:
        if type(arg) is dict:
            op_name = arg["op"]
            if op_name in _reduction_ops:

                if op_name[0:3] == "arg":
                    argminmax = True

                # To reduce a whole tensor (axis=None) reduce along each axis in succession.
                if arg.get("axis",None) not in (0,1):
                    raise ValueError("Only reduction along an axis currently supported")

                # Keep axis values consistent within the same kernel
                if reduction is True:
                    if arg["axis"] != axis:
                        raise ValueError("Reduction only allowed along one axis per kernel.")
                else:
                    reduction = True
                    axis = arg["axis"]
            elif op_name == "onehot":
                takeop = True

        elif isinstance(arg, ng.GPUTensor):
            if len(arg.shape) < 2 or (len(arg.shape) == 2 and (arg.shape[0] == 1 or arg.shape[1] == 1)):
                broadcast = True
            elif arg.is_trans:
                transpose = True
            elif arg.take_array:
                takeop = True
            elif not arg.is_contiguous:
                contiguous = False

    # If reducing along axis 0 we need to reverse all strides.
    # Each block gets a column and the threads work down the columns.
    stride_order = 1 if axis == 1 else -1

    for arg in args:

        # Array operand
        if isinstance(arg, ng.GPUTensor):

            # for complex operations, use the native dimensions
            if broadcast or reduction or transpose or takeop or not contiguous:
                if len(arg.shape) == 2:
                    shape   = arg.shape
                    strides = list(arg.strides[::stride_order])
                else:
                    raise ValueError("Operations that are not simple elementwise are only currently supported in 2 dimensions.")

            # use more efficient 2d dimensions if this is a plain ew op.
            else:
                shape, strides = _get_fast_ew_dims(arg.size)
                strides = list(strides[::stride_order])

            # If same array is passed in multiple times to expression,
            # consolidate them into one kernel argument.
            if arg in array_ids:
                indx = array_ids[arg]
            else:

                # The first array passed in should be the output.
                # It's ok if this array is duplicated as the first instance
                # needs to be a mutable pointer.
                # A subsequent instance of out (if present) will be a const pointer.
                if out is None:
                    out  = arg
                    indx = arg_cnt
                else:
                    indx = array_ids[arg] = arg_cnt
                arg_cnt += 1

                # support broadcast
                if shape[0] == 1:
                    strides[1-axis] = 0
                if shape[1] == 1:
                    strides[axis]   = 0

                kernel_args.extend((arg.gpudata, strides[0], strides[1]))

                # fancy indexing/take
                if arg.take_array:
                    kernel_args.append(arg.take_array[0].gpudata)

            # swap the take axis when reducing axis=0
            # also add 1 to distinguish between no take operations
            if arg.take_array:
                if axis != 1:
                    take_axis = 2 - arg.take_array[1]
                else:
                    take_axis = arg.take_array[1] + 1
            # no take operation
            else:
                take_axis = 0

            type_args.append((ng.GPUTensor, indx, arg.dtype.str[1:], take_axis))

            shape_stack.append(shape)

        # Constant operand
        elif type(arg) in (int, float):

            arg = float(arg)
            if arg in const_ids:
                indx = const_ids[arg]
            else:
                indx = const_ids[arg] = arg_cnt
                arg_cnt += 1

                kernel_args.append(arg)

            type_args.append((float, indx))
            shape_stack.append((1,1))

        # Operation
        elif type(arg) is dict:

            op_name = arg["op"]

            if op_name in _float_ops:

                # we need to do the shape arithemtic for the current operation
                max_shape = [1,1]
                for op_num in range(_float_ops[op_name][0]):
                    shape = shape_stack.pop()
                    for i in range(2):
                        if shape[i] != max_shape[i]:
                            # support broadcast
                            # TODO: don't allow output tensor itself to be broadcastable.
                            # The final output is fine as a broadcast, for example assigning a constant.
                            # You just dont want a tensor being assigned to a smaller shape.
                            if shape[i] == 1 or max_shape[i] == 1:
                                max_shape[i] = max(max_shape[i], shape[i])
                            else:
                                raise TypeError("Input shape:%s not compatible" % (shape,))

                if op_name == "assign":

                    # the axis dim is the thread loop stop condition
                    kernel_args.append(max_shape[axis])

                    rounding = out.rounding

                    # support rounding to arbitrary mantissa size
                    if rounding:
                        # convert bool to some default mantissa
                        if rounding is True:
                            rounding = 10
                        elif out.dtype.type is np.float32:
                            rounding = min(rounding,15)
                        elif out.dtype.type is np.float16:
                            rounding = min(rounding,10)

                        kernel_args.append(max(rounding,1))

                    # speed up deep reduction by using more than 32 threads
                    if reduction and not argminmax:
                        if   red_depth >= 4096: threads = 1024
                        elif red_depth >= 2048: threads = 512
                        elif red_depth >= 1024: threads = 256
                        elif red_depth >=  512: threads = 128
                        elif red_depth >=  256: threads = 64

                    # speed up deep broadcast by using more than 32 threads
                    elif not (reduction or transpose) and max_shape[1] >= 512:
                        threads = 256

                    type_args.append((op_name, op_cnt, rounding > 0, threads))

                elif op_name == "onehot":

                    # flip the one hot axis if reducing axis=0
                    hot_axis = arg["axis"] if axis else 1 - arg["axis"]

                    type_args.append((op_name, op_cnt, hot_axis))
                    shape_stack.append(max_shape)
                    kernel_args.append(arg["idx"].gpudata)

                else:
                    type_args.append((op_name, op_cnt))
                    shape_stack.append(max_shape)

            elif op_name in _reduction_ops:

                shape = list(shape_stack.pop())

                red_depth = max(red_depth, shape[axis])

                # Allow a new axis size if doing post reduction broadcast.
                # So we need to know the axis size prior to reduction.
                kernel_args.append(shape[axis])
                type_args.append((op_name, op_cnt))

                # reduce the current shape
                shape[axis] = 1

                # udpate the current shape state
                shape_stack.append(shape)

            else:
                raise TypeError("%s is not a valid operation" % op_name)

            op_cnt += 1

        else:
            raise TypeError("args must be instance of GPUTensor, int, float, or dict (for operators)")

    # for s in argsprint:   print s
    # for s in kernel_args: print s
    # for s in type_args:   print s

    # get or create the kernel in the memoize cache
    kernel = _get_compound_kernel(tuple(type_args))

    #import ipdb; ipdb.set_trace()

    shared = threads * 4 if reduction and threads > 32 else 0

    if out.backend.bench > 1:
        repeat = out.backend.bench
        start, end = ng._get_events()
        start.record(out.backend.stream)
    else:
        repeat = 1

    for r in range(repeat):

        # call the kernel with the number of blocks set as the size of the off-axis
        # Maxwell does well with 32 thread sized blocks, no need to autotune.
        #for a in kernel_args: print a
        kernel.prepared_async_call((max_shape[1-axis],1,1), (threads,1,1), out.backend.stream, *kernel_args, shared_size=shared)

    if out.backend.bench > 1:
        end.record(out.backend.stream)
        end.synchronize()
        msecs = end.time_since(start) / repeat
        print("%7.3f msecs shape(%d,%d) blk,thd(%d,%d) %s" % (msecs, max_shape[0], max_shape[1], max_shape[1-axis], threads, kernel.name))

    return out

# quick wrapper to convert raw fp32 scratch data to a destination tensor
def fp32_convert(src_data, dest_tensor):

    shape, strides = _get_fast_ew_dims(dest_tensor.size)
    kernel_args    = [0, dest_tensor.gpudata, strides[0], strides[1], src_data, strides[0], strides[1], shape[1] ]
    kernel         = _get_compound_kernel((
        (ng.GPUTensor,0,dest_tensor.dtype.str[1:],0),
        (ng.GPUTensor,1,"f4",0),
        ('assign', 0, False, 32)))
    kernel.prepared_async_call((shape[0],1,1), (32,1,1), dest_tensor.backend.stream, *kernel_args)


_transpose_kernel = r"""
__global__ void transpose(%(type)s* out, const %(type)s* in, int rows, int cols)
{
    __shared__ %(type)s tile[32][33];

    int tx = threadIdx.x;
    int ty = threadIdx.y;
    int bx = blockIdx.x;
    int by = blockIdx.y;
    int gx = bx * 32 + tx;
    int gy = by * 32 + ty;

    for (int j = 0; j < 32; j += 8)
    {
        int gy8 = gy + j;
        if (gy8 < rows && gx < cols)
            tile[ty + j][tx] = in[gy8*cols + gx];
    }
    __syncthreads();

    gx = by * 32 + tx;
    gy = bx * 32 + ty;

    for (int j = 0; j < 32; j += 8)
    {
        int gy8 = gy + j;
        if (gy8 < cols && gx < rows)
            out[gy8*rows + gx] = tile[tx][ty + j];
    }
}
"""
@context_dependent_memoize
def _get_transpose_kernel(dtype):

    code   = _transpose_kernel % _ew_types[dtype]
    module = SourceModule(code)
    kernel = module.get_function("transpose")
    kernel.prepare("PPII")
    return kernel


_shuffle_kernel = r"""
__global__ void dimShuffle(
    %(type)s* out, const %(type)s* in,
    int TRSK, int RSK, int SK, int K,
    int TRSC, int RSC, int SC, int C,
    int RS, int magic_RS, int shift_RS,
    int S,  int magic_S,  int shift_S)
{
    __shared__ %(type)s tile[32][33];

    int tx  = threadIdx.x;
    int ty  = threadIdx.y;
    int bk  = blockIdx.x;
    int bc  = blockIdx.y;
    int trs = blockIdx.z;

    int k  = bk * 32 + tx;
    int c  = bc * 32 + ty;

    int t  = magic_RS * trs; t >>= shift_RS;
    int rs = trs - t*RS;

    int r = magic_S * rs; r >>= shift_S;
    int s = rs - r*S;

    for (int j = 0; j < 32; j += 8)
    {
        int cj = c + j;
        if (cj < C && k < K)
            tile[ty + j][tx] = in[ cj*TRSK + t*RSK + r*SK + s*K + k ];
    }
    __syncthreads();

    k = bk * 32 + ty;
    c = bc * 32 + tx;

    for (int i = 0; i < 32; i += 8)
    {
        int ki = k + i;
        if (ki < K && c < C)
            out[ ki*TRSC + t*RSC + r*SC + s*C + c ] = tile[tx][ty + i];
    }
}
"""
@context_dependent_memoize
def _get_shuffle_kernel(dtype):

    code   = _shuffle_kernel % _ew_types[dtype]
    module = SourceModule(code)
    kernel = module.get_function("dimShuffle")
    kernel.prepare("PPIIIIIIIIIIIIII")
    return kernel

_compensated_sum = r"""

%(common)s

__global__ void compensated_sum(unsigned* rand_state,
          %(type)s* a_sum,
          %(type)s* a_cmp,
    const %(type)s* a_add,
    float cmp_scale, float add_scale,
    int row_strd, int col_strd, int n, int mantissa_bits)
{
    const int tid = threadIdx.x;
    const int bid = blockIdx.x;

    int offset = bid * row_strd + tid * col_strd;
    int inc    = 32 * col_strd;

    a_sum += offset;
    a_cmp += offset;
    a_add += offset;

    %(inits)s

    for (int i = tid; i < n; i += 32)
    {
        float s32 = %(cvt)s(__ldg((const %(type)s*)a_sum));
        float c32 = %(cvt)s(__ldg((const %(type)s*)a_cmp));
        float a32 = %(cvt)s(__ldg(a_add));

        // Adjust amount to add by previous compensation
        float y32 = a32 * add_scale - c32 * cmp_scale;

        // Do the accumulation and truncate to the storage type
        float rnd_sum = s32 + y32;
        %(rnd_sum)s

        // Convert accumulation back to fp32 so we can do more math on it
        float t32 = %(cvt)s(t16);

        // recover the low order bits that were lost in the truncation
        float rnd_cmp = (t32 - s32) - y32;
        %(rnd_cmp)s

        *a_sum = t16;
        *a_cmp = c16;

        a_sum += inc;
        a_cmp += inc;
        a_add += inc;
    }
    %(finish)s
}
"""

@context_dependent_memoize
def _get_compensated_sum_kernel(dtype, rounding):

    template_vals = dict()
    for key in ("common", "inits", "finish"):
        template_vals[key] = ""

    if dtype == "f2":
        template_vals["common"] += _common_fp16_to_fp32

    if rounding:
        template_vals["common"] += _common_urand_gen
        template_vals["common"] += _common_round["nearest"].get(dtype,"")
        template_vals["inits" ] += _init_rand_func + _init_rand_round_func
        template_vals["finish"] += _finish_rand_func
        mode = "random"
    else:
        mode = "nearest"

    template_vals["common"] += _common_round[mode].get(dtype,"")

    template_vals["type"] = _ew_types[dtype]["type"]
    template_vals["cvt"]  = _ew_types[dtype]["cvt"]

    no_op = "float {0} = {1};"

    rnd_sum = _ew_strings["round"][  mode   ].get(dtype, no_op)
    rnd_cmp = _ew_strings["round"]["nearest"].get(dtype, no_op)

    template_vals["rnd_sum"] = rnd_sum.format("t16","rnd_sum")
    template_vals["rnd_cmp"] = rnd_cmp.format("c16","rnd_cmp")

    code = _compensated_sum % template_vals

    # f = open("compensated_sum.cu", "w")
    # print >>f, code
    # f.close()

    module = SourceModule(code)
    kernel = module.get_function("compensated_sum")
    kernel.prepare("PPPPffiiii")
    return kernel


nrv_re  = re.compile(r'nervanagpu\.py$')
name_re = re.compile(r'\W')

def _get_kernel_name():

    names = ["kernel",]
    for frame in tb.extract_stack():
        if nrv_re.search(frame[0]):
            break
        caller = frame[0:2]

    file_path, file_name = os.path.split(caller[0])
    path1, path2         = os.path.split(file_path)
    file_base, ext       = os.path.splitext(file_name)

    for name in (path2, file_base, ext):
        name = name_re.sub("", name)
        if name:
            names.append(name)
    #names.append(str(caller[1]))

    return names


