import struct
import onnx
import onnx.helper

# ── Compatibility patch for onnx_graphsurgeon ────────────────────────────────
# onnx_graphsurgeon references onnx.helper.float32_to_bfloat16 at load time.
# This was removed in onnx >= 1.16. We inject it globally before any imports.
if not hasattr(onnx.helper, "float32_to_bfloat16"):
    def _float32_to_bfloat16(fval: float) -> int:
        """BFloat16 = upper 16 bits of IEEE 754 float32 (round-to-nearest-even)."""
        bits = struct.unpack("<I", struct.pack("<f", float(fval)))[0]
        lsb = (bits >> 16) & 1
        rounding_bias = 0x7FFF + lsb
        bits += rounding_bias
        return bits >> 16
    onnx.helper.float32_to_bfloat16 = _float32_to_bfloat16
