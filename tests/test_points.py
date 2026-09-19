import numpy as np

from plm.engine import PointSet, dedupe, fill_zero_z_along_line


def test_dedupe_keeps_first_and_reports_pairs():
    ps = PointSet.from_arrays(
        [0, 1, 0.0004, 2, 1.0004], [0, 1, 0.0002, 2, 1.0], [10, 11, 99, 12, 98],
        ids=["a", "b", "c", "d", "e"],
    )
    out, rep = dedupe(ps, tol=0.001)
    assert len(out) == 3
    assert out.ids == ["a", "b", "d"]
    assert rep.removed_duplicates == 2
    assert sorted(rep.duplicate_pairs) == [(0, 2), (1, 4)]
    assert rep.kept == 3


def test_dedupe_zero_z():
    ps = PointSet.from_arrays([0, 1, 2], [0, 1, 2], [5, 0, 7])
    out, rep = dedupe(ps, drop_zero_z=True)
    assert len(out) == 2 and rep.removed_zero_z == 1
    out2, rep2 = dedupe(ps, drop_zero_z=False)
    assert len(out2) == 3 and rep2.removed_zero_z == 0


def test_dedupe_empty():
    out, rep = dedupe(PointSet.empty())
    assert len(out) == 0 and rep.kept == 0


def test_fill_zero_z_along_line_by_distance():
    line = np.array([[0, 0, 100.0], [10, 0, 0.0], [30, 0, 0.0], [40, 0, 104.0]])
    out = fill_zero_z_along_line(line)
    assert np.allclose(out[:, 2], [100, 101, 103, 104])
    # leading unknown left untouched, original not modified
    line2 = np.array([[0, 0, 0.0], [10, 0, 100.0], [20, 0, 0.0], [30, 0, 110.0]])
    out2 = fill_zero_z_along_line(line2)
    assert out2[0, 2] == 0.0 and np.isclose(out2[2, 2], 105.0)
    assert line2[2, 2] == 0.0


def test_pointset_concat_subset():
    a = PointSet.from_arrays([0], [0], [1], ids=["1"], remarks=["a"])
    b = PointSet.from_arrays([1, 2], [1, 2], [2, 3], ids=["2", "3"], remarks=["b", "c"])
    c = PointSet.concat([a, b])
    assert len(c) == 3 and c.ids == ["1", "2", "3"]
    s = c.subset(np.array([False, True, True]))
    assert s.remarks == ["b", "c"]
