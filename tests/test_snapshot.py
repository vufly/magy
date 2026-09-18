from pathlib import Path

from magy.testing.snapshot import compare_snapshots, take_snapshot


def test_take_and_compare_snapshots(tmp_path: Path):
    target = tmp_path / "test_dir"
    target.mkdir()
    file_a = target / "a.txt"
    file_a.write_text("hello", encoding="utf-8")

    snap1 = take_snapshot(target)
    assert "a.txt" in snap1
    assert snap1["a.txt"]["size"] == 5

    # Identical comparison
    diff1 = compare_snapshots(snap1, snap1)
    assert diff1["identical"] is True
    assert len(diff1["added"]) == 0
    assert len(diff1["modified"]) == 0

    # Modify a file and add a file
    file_a.write_text("hello world!", encoding="utf-8")
    file_b = target / "b.txt"
    file_b.write_text("new", encoding="utf-8")

    snap2 = take_snapshot(target)
    diff2 = compare_snapshots(snap1, snap2)
    assert diff2["identical"] is False
    assert "b.txt" in diff2["added"]
    assert "a.txt" in diff2["modified"]

    # Delete file
    file_b.unlink()
    snap3 = take_snapshot(target)
    diff3 = compare_snapshots(snap2, snap3)
    assert "b.txt" in diff3["removed"]
