import sqlite3
import math
import glob
import pandas as pd
import os
import subprocess
import sys


# ─────────────────────────────────────────────────────────────────────────────
# STANDS WITH KNOWN SIMFIRE MORTALITY ISSUES
# Add stand IDs here to disable tree mortality (field 5 = 0) for that stand.
# Symptom: SIGFPE / code 3 crash when mortality is enabled.
# ─────────────────────────────────────────────────────────────────────────────
MORTALITY_SKIP_STANDS = set()   # list is in config.yaml: fvs.mortality_skip_stands

# ─────────────────────────────────────────────────────────────────────────────
# KEYWORD FORMATTER
# ─────────────────────────────────────────────────────────────────────────────

def kw(keyword, *fields):
    """
    Format an FVS keyword record with proper 10-column fixed fields.

    Rules from FVS Essential Guide:
        - Keyword left-justified in columns 1-10
        - Each parameter field is exactly 10 columns wide
        - Integers must be right-justified within their field
        - Floats and alphanumeric can appear anywhere (right-justified here)
        - None → blank field (10 spaces) → FVS uses default value
        - Zero must be entered explicitly if zero is desired (blank != zero)

    Parameters
    ----------
    keyword : str
        FVS keyword (e.g. 'SimFire', 'FIXMORT').
    *fields : int | float | str | None
        Parameter values. Pass None to leave a field blank (use FVS default).

    Returns
    -------
    str
        Properly formatted keyword record string.
    """
    line = f"{keyword:<10}"
    for field in fields:
        if field is None:
            line += " " * 10
        elif isinstance(field, bool):
            line += f"{int(field):>10}"
        elif isinstance(field, int):
            line += f"{field:>10}"
        elif isinstance(field, float):
            formatted = f"{field:.4f}".rstrip("0").rstrip(".")
            line += f"{formatted:>10}"
        else:
            line += f"{str(field):<10}"
    return line


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def get_stand_ids(db_path):
    connection = sqlite3.connect(db_path, timeout=10)
    cursor = connection.cursor()
    cursor.execute("SELECT DISTINCT STAND_ID FROM FVS_StandInit")
    results = cursor.fetchall()
    stand_ids = [result[0] for result in results]
    cursor.close()
    connection.close()
    return stand_ids


def remove_inf_db(directory, database):
    """Remove Inf values from database. Utility function only."""
    table_name = "FVS_StandInit"
    database_file = os.path.join(directory, database)
    connection = sqlite3.connect(database_file)
    cursor = connection.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [column[1] for column in cursor.fetchall()]
    print(columns)
    cursor.execute("UPDATE FVS_StandInit SET Age = 0 WHERE Age = 'Inf'")
    connection.commit()
    cursor.close()
    connection.close()


# ─────────────────────────────────────────────────────────────────────────────
# KEYWORD BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def build_keywords(db_name, db_index, stand_ids, df_treat, switch_treatment,
                   mortality_skip_stands=None, fcfg=None):
    if mortality_skip_stands is None:
        mortality_skip_stands = set()

    treat_ids = df_treat["POLYGON_ID"].astype(str).str.strip()
    treat_pct  = df_treat.set_index(treat_ids)["PERCENT"]

    matched = [sid for sid in stand_ids if str(sid).strip() in treat_ids.values]
    print(f"  • Treated stands in this db: {len(matched)} / {len(stand_ids)}")

    key_lines = []
    for sid in stand_ids:
        pct_treat = 0
        sid_str = str(sid).strip()
        if switch_treatment == 1 and sid_str in treat_ids.values:
            pct_treat = math.ceil(treat_pct.loc[sid_str])

        key_lines.append(
            write_key(db_name, sid, db_index, switch_treatment, pct_treat,
                      mortality_skip_stands, fcfg)
        )

    key_lines.append("\nSTOP\n")
    return "\n\n".join(key_lines)


def write_key(db_name, sid, file_num, switch_treatment, pct_treat,
              mortality_skip_stands=None, fcfg=None):
    """
    Build the complete keyword block for a single stand.

    SimFire fields (BC variant, metric):
        field 1 : year
        field 2 : wind speed (km/h)
        field 3 : moisture level (1=extra dry ... 5=humid)
        field 4 : temperature (C)
        field 5 : mortality code (0=off, 1=FFE estimates mortality)
        field 6 : percent of stand area burned
        field 7 : season (1=early spring, 2=before greenup,
                          3=after greenup, 4=fall)
    """
    if mortality_skip_stands is None:
        mortality_skip_stands = set()

    wf = fcfg["wildfire"]   # config: fvs.wildfire
    # Mortality flag: 0 if stand is in skip list, 1 otherwise
    mort = 0 if str(sid) in mortality_skip_stands else 1
    if mort == 0:
        print(f"    ⚠ Stand {sid}: mortality disabled (in skip list)")

    # ── Database input block ─────────────────────────────────────────────────
    key_init = (
        f"STDIDENT\n"
        f"{sid}\n"
        f"StandCN\n"
        f"{sid}\n"
        f"\n"
        f"DATABASE\n"
        f"DSNin\n"
        f"{db_name}.db\n"
        f"StandSQL\n"
        f"SELECT * FROM FVS_StandInit WHERE Stand_ID = '{sid}'\n"
        f"EndSQL\n"
        f"TreeSQL\n"
        f"SELECT * FROM FVS_TreeInit WHERE Stand_ID = '{sid}'\n"
        f"EndSQL\n"
        f"End\n"
    )

    # ── Timing block ─────────────────────────────────────────────────────────
    key_timing = "\n".join([
        kw("InvYear",  fcfg["inventory_year"]),
        kw("TimeInt",  None, 5),
        kw("TimeInt",  1,    3),
        kw("NumCycle", fcfg["num_cycles"]),
    ])

    # ── Fire extension block ─────────────────────────────────────────────────
    if pct_treat > 0:
        fire_lines = [
            "STRCLASS",
            "FMIn",
            "!Exten:fire Title:Prescribed burn in spring + severe fire in summer",
            kw("SimFire", wf["year"], wf["wind_kmh"], wf["moisture_code"], wf["temperature_c"],
               mort if wf["mortality"] else 0, wf["percent_area"], wf["season"]),  # megafire: mort conditional
            kw("flameadj", wf["year"], float(fcfg["flame_adjust_treated"])),  # flame adjust for areas that have seen IFS
        ]
    else:
        fire_lines = [
            "STRCLASS",
            "FMIn",
            "!Exten: Severe fire in summer",
            kw("SimFire", wf["year"], wf["wind_kmh"], wf["moisture_code"], wf["temperature_c"],
               mort if wf["mortality"] else 0, wf["percent_area"], wf["season"]),
        ]

    fire_lines += [
        kw("CARBCALC", 0, 1),
        kw("CARBREPT", 2),
        "End",
    ]
    key_fire = "\n".join(fire_lines)

    # ── Database output block ────────────────────────────────────────────────
    out_db = f"FVS_fire_out_{file_num}_{switch_treatment}.db"
    key_db_out = "\n".join([
        "DATABASE",
        "DSNOut",
        out_db,
        "Summary",
        kw("CARBREDB", 1),
        "End",
    ])

    return "\n".join([
        key_init,
        key_timing,
        "",
        key_fire,
        "",
        key_db_out,
        "",
        "Process",
    ])


# ─────────────────────────────────────────────────────────────────────────────
# FVS RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def call_fvs(path_dir, db_name, db_index, switch_treatment, exe_path, timeout=None):
    executable_path = exe_path   # config: executables.fvs

    inputs = [
        f"key_fire_{db_index}_{switch_treatment}.txt\n",
        f"{db_name}\n",
        f"main_fire_out_{db_index}_{switch_treatment}.txt\n",
        "\n", "\n", "\n",
    ]

    process = subprocess.Popen(
        executable_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        cwd=path_dir,
    )

    try:
        for input_text in inputs:
            process.stdin.write(input_text)
            process.stdin.flush()

        process.stdin.close()
        stdout, stderr = process.communicate(timeout=timeout)

        if process.returncode != 0:
            print(f"  ⚠ FVS run {db_index} failed (code {process.returncode})")
            print(stderr)
        else:
            print(f"  ✓ FVS run {db_index} completed successfully")

    except subprocess.TimeoutExpired:
        process.kill()
        print(f"  ⏱ FVS run {db_index} timed out after {timeout}s")
    except Exception as e:
        print(f"  ❌ Error running FVS case {db_index}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main(cfg, year, switch_treatment):
    """
    Iterate over all FVS_in_**.db files in path_dir,
    generate one keyword file per DB, and call FVS.
    """
    fcfg = cfg["fvs"]
    path_dir = cfg.path("fvs_dir", year=year)
    df_treatment = pd.read_excel(cfg.treatment_table(year))

    db_files = glob.glob(os.path.join(path_dir, "FVS_in_**.db"))
    if not db_files:
        raise FileNotFoundError(f"No .db files found in {path_dir}")

    print(f"Found {len(db_files)} database(s).")

    for db_path in db_files:
        db_name  = os.path.splitext(os.path.basename(db_path))[0]
        db_index = db_name[-2:]
        print(f"\n=== Processing {db_name}.db ===")

        # Remove existing output db to avoid appending
        out_db = os.path.join(
            path_dir, f"FVS_fire_out_{db_index}_{switch_treatment}.db"
        )
        try:
            os.remove(out_db)
        except FileNotFoundError:
            pass

        stand_ids = get_stand_ids(db_path)
        print(f"  • {len(stand_ids)} stand(s) found")

        key_text = build_keywords(
            db_name, db_index, stand_ids, df_treatment, switch_treatment,
            mortality_skip_stands={str(s) for s in fcfg["mortality_skip_stands"]},
            fcfg=fcfg
        )

        key_file = os.path.join(
            path_dir, f"key_fire_{db_index}_{switch_treatment}.txt"
        )
        with open(key_file, "w") as f:
            f.write(key_text)
        print(f"  • Keyword file written -> {key_file}")

        try:
            call_fvs(path_dir, db_name, db_index, switch_treatment, cfg.exe("fvs"),
                     timeout=fcfg["run_timeout_s"])
        except RuntimeError as exc:
            print(f"  ⚠ {exc}")

    print("\n🎉 All databases processed.")