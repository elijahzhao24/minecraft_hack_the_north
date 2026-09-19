import simd

public enum MatrixWireFormatter {
    public static func rowMajor(_ matrix: simd_double3x3) -> [Double] {
        // SIMD stores columns; spelling out each mathematical row prevents a silent transpose.
        [
            matrix.columns.0.x, matrix.columns.1.x, matrix.columns.2.x,
            matrix.columns.0.y, matrix.columns.1.y, matrix.columns.2.y,
            matrix.columns.0.z, matrix.columns.1.z, matrix.columns.2.z,
        ]
    }

    public static func rowMajor(_ matrix: simd_double4x4) -> [Double] {
        // Wire matrices are row-major even though simd_double4x4 is column-major in memory.
        [
            matrix.columns.0.x, matrix.columns.1.x, matrix.columns.2.x, matrix.columns.3.x,
            matrix.columns.0.y, matrix.columns.1.y, matrix.columns.2.y, matrix.columns.3.y,
            matrix.columns.0.z, matrix.columns.1.z, matrix.columns.2.z, matrix.columns.3.z,
            matrix.columns.0.w, matrix.columns.1.w, matrix.columns.2.w, matrix.columns.3.w,
        ]
    }
}
