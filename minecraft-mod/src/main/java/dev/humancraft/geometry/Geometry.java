package dev.humancraft.geometry;

/** Shared numeric tolerances. One declared epsilon is used by every intersection routine. */
public final class Geometry {
	/** Absolute tolerance for ray parameters, tangency and orthonormality checks. */
	public static final double EPSILON = 1e-9;
	/** Tolerance for OBB axis orthogonality (contract: pairwise dot within 1e-4). */
	public static final double AXIS_DOT_TOLERANCE = 1e-4;
	/** Tolerance for |det(axes)| == 1 (contract: within 1e-3). */
	public static final double AXIS_DET_TOLERANCE = 1e-3;
	/** Tolerance for axis norm == 1. */
	public static final double AXIS_NORM_TOLERANCE = 1e-3;

	private Geometry() {}

	public static boolean nearlyEqual(double a, double b, double tol) {
		return Math.abs(a - b) <= tol;
	}
}
