from typer.testing import CliRunner

from era835.cli import app

runner = CliRunner()


def test_generate_load_reconcile_denials_quarantine(tmp_path):
    data, wh = tmp_path / "data", tmp_path / "wh"
    result = runner.invoke(app, ["generate", "--out", str(data), "--seed", "835"])
    assert result.exit_code == 0 and "3000 claim segments" in result.output

    result = runner.invoke(app, ["load", str(data / "files"), "-w", str(wh)])
    assert result.exit_code == 0
    assert "36 loaded, 0 partial, 4 quarantined, 1 duplicate" in result.output
    assert "0 pending reversals" in result.output

    deposits = str(data / "deposits.csv")
    result = runner.invoke(app, ["reconcile", "--deposits", deposits, "-w", str(wh)])
    assert result.exit_code == 0
    assert result.output.count("tied") == 36 and result.output.count("quarantined") == 4

    result = runner.invoke(app, ["denials", "-w", str(wh)])
    assert result.exit_code == 0 and "CO-50" in result.output.splitlines()[2]

    result = runner.invoke(app, ["quarantine", "-w", str(wh)])
    assert "claim_balance" in result.output and "segment_count" in result.output


def test_reconcile_exits_nonzero_on_a_difference(tmp_path):
    data, wh = tmp_path / "data", tmp_path / "wh"
    runner.invoke(app, ["generate", "--out", str(data)])
    runner.invoke(app, ["load", str(data / "files"), "-w", str(wh)])
    lines = (data / "deposits.csv").read_text().splitlines()
    rows = [line.rsplit(",", 1)[0] + ",1.00" for line in lines[1:]]
    altered = tmp_path / "deposits.csv"
    altered.write_text("\n".join([lines[0], *rows]) + "\n")
    result = runner.invoke(app, ["reconcile", "--deposits", str(altered), "-w", str(wh)])
    assert result.exit_code == 1 and "difference" in result.output
