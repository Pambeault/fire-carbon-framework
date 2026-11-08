import sqlite3
import math
import glob
import pandas as pd
import os
import subprocess
import sys

### Utility functions
def get_stand_ids(db_path):
    connection = sqlite3.connect(db_path, timeout=10)
    cursor = connection.cursor()

    # Select unique values from the "uniqueID" column of the "standinit" table
    cursor.execute("SELECT DISTINCT STAND_ID FROM FVS_StandInit")
    results = cursor.fetchall()

    # Extract the unique IDs from the results and store them in an array
    stand_ids = [result[0] for result in results]

    cursor.close()
    connection.close()

    return stand_ids

def remove_inf_db(directory, database):  # Remove inf. values from database. Only a Utility function
    table_name = "FVS_StandInit"
    database_file = os.path.join(directory, database)
    connection = sqlite3.connect(database_file)
    cursor = connection.cursor()

    # Get the column names from the table
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [column[1] for column in cursor.fetchall()]
    print(columns)

    # # Generate the SQL update statement for each column of the table
    # for column in columns:
    #     cursor.execute(f"UPDATE {table_name} SET {column} = 0 WHERE {column} = 'Inf'")
    cursor.execute("UPDATE FVS_StandInit SET Age = 0 WHERE Age = 'Inf'")

    # Commit and close
    connection.commit()
    cursor.close()
    connection.close()

    return

### === Working functions ===

# --------------------------------------------------------------------
# helper: build one keyword string for a db and a treatment DataFrame
# --------------------------------------------------------------------
def build_keywords(db_path, db_index, stand_ids, df_treat, switch_treatment, fcfg):
    """
    Return the full keyword text for a single FVS run.
    """
    key_lines = []

    for sid in stand_ids:
        pct_treat = 0
        if switch_treatment == 1 and sid in df_treat["POLYGON_ID"].values:
            pct_treat = math.ceil(
                df_treat.loc[df_treat["POLYGON_ID"] == sid, "PERCENT"].values[0]
            )

        key_lines.append(write_key(db_path, sid, db_index, switch_treatment, pct_treat, fcfg))

    key_lines.append("\nSTOP\n")
    return "\n\n".join(key_lines)

def write_key(db_name, id, file_num, switch_treatment, pct_treat, fcfg):
    # Settings from config.yaml (fvs.*). Fixed-column spacing kept exactly as in the paper.
    inv_year = fcfg["inventory_year"]
    n_cycles = fcfg["num_cycles"]
    pb = fcfg["prescribed_burn"]
    pb_year, pb_wind, pb_moist = pb["year"], pb["wind_kmh"], pb["moisture_code"]
    pb_temp, pb_mort, pb_season = pb["temperature_c"], pb["mortality"], pb["season"]
    pb_flame = pb["flame_adjust"]
    # Define content variables for each section of the keyword file
    key_init = f'''STDIDENT
{id}
StandCN
{id}

DATABASE
DSNin
{db_name}.db
StandSQL
SELECT * FROM FVS_StandInit WHERE Stand_ID = '{id}'
EndSQL
TreeSQL
SELECT * FROM FVS_TreeInit WHERE Stand_ID = '{id}'
EndSQL
End
'''
    # Keyword different for treatment trigger or not
    # Using simfire
    # Year / wind speed / moisture level (1 = extra dry, 2 dry, 3 humid, etc.) / temperature in F/ Mortality code (0 = TURN OFF FFE MORTALITY, 1 = FFE ESTIMATES MORTALITY) /
    # Percentage of stand area burned / season of fire (1 = EARLY SPRING (COMPACT LEAVES), 2 = BEFORE GREENUP, 3 = AFTER GREENUP, 4 = FALL)
    if pct_treat > 0:
        key_timestamp = f'''
InvYear       {inv_year}
TimeInt                 5 
TimeInt       1        3 
NumCycle     {n_cycles}

STRCLASS
FMIn
!Exten:fire Title:Prescribed burn
SimFire   {pb_year:>10}{pb_wind:>10}{pb_moist:>10}{pb_temp:>10}{pb_mort:>10}{pct_treat:>10}{pb_season:>10}
Flameadj  {pb_year:>10}{pb_flame:>10}
CARBREPT        2
CARBCALC        0        1       0
FUELOUT         0
FUELREPT
POTFIRE
End

DATABASE
DSNOut
FVS_out_{file_num}_{switch_treatment}.db
Summary
STRCLSDB       1
CARBREDB       1
FuelsOut       1
FUELREDB       1
PotfirDB       1
End
'''
    else:
        key_timestamp = f'''
InvYear       {inv_year}
TimeInt                 5 
TimeInt       1        3 
NumCycle     {n_cycles}

STRCLASS
FMIn
CARBREPT        2
CARBCALC        0        1       0
FUELOUT         0
POTFIRE
End

DATABASE
DSNOut
FVS_out_{file_num}_{switch_treatment}.db
Summary
STRCLSDB       1
CARBREDB       1
FuelsOut       1
FUELREDB       1
PotfirDB       1
End
'''


    # Combine content variables into the final content
    key_content = f"{key_init}\n{key_timestamp}"

    key_content = f"{key_content}\nProcess\n"   # Close the statement with 'process'
    return key_content

def call_fvs(path_dir, standdata, db_index, switch_treatment, exe_path, timeout=None):
    # Path to the executable file (config: executables.fvs)
    executable_path = exe_path

    # Sequential inputs (including final empty lines)
    inputs = [
        f"key_{db_index}_{switch_treatment}.txt\n",
        f"{standdata}\n",
        f"main_out_{db_index}_{switch_treatment}.txt\n",
        "\n",
        "\n",
        "\n"
    ]

    # Start the executable process
    process = subprocess.Popen(
        executable_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        cwd=path_dir
    )

    try:
        for input_text in inputs:
            process.stdin.write(input_text)
            process.stdin.flush()

        process.stdin.close()  # signal that no more input is coming

        # Wait for the process to finish (with optional timeout)
        stdout, stderr = process.communicate(timeout=timeout)

        if process.returncode != 0:
            print(f"⚠ FVS run {db_index} failed with code {process.returncode}")
            print(stderr)
        else:
            print(f"✓ FVS run {db_index} completed successfully")

    except subprocess.TimeoutExpired:
        process.kill()
        print(f"⏱ FVS run {db_index} timed out after {timeout} seconds.")
    except Exception as e:
        print(f"❌ Error running FVS case {db_index}: {e}")


def main(cfg, year, switch_treatment):
    """
        Iterate over all *.db files in *path_dir*,
        generate one keyword file per DB, and call FVS.
        """

    # Excel with treatment percentages
    fcfg = cfg["fvs"]
    path_dir = cfg.path("fvs_dir", year=year)
    df_treatment = pd.read_excel(cfg.treatment_table(year))

    # Discover every .db file
    db_files = glob.glob(os.path.join(path_dir, "FVS_in_**.db"))
    if not db_files:
        raise FileNotFoundError(f"No .db files found in {path_dir}")

    print(f"Found {len(db_files)} database(s).")

    for db_path in db_files[:]:

        db_name = os.path.splitext(os.path.basename(db_path))[0]
        print(f"\n=== Processing {db_name}.db ===")
        db_index = db_name[-2:]  # Get the last 2 characters

        try:
            os.remove(os.path.join(path_dir, f"FVS_out_{db_index}_{switch_treatment}.db"))  # Remove existing .db diles otherwise append to it
        except FileNotFoundError:
            pass

        stand_ids = get_stand_ids(db_path)
        print(f"  • {len(stand_ids)} stand(s) found")

        # Build keyword text
        key_text = build_keywords(
            db_name, db_index, stand_ids, df_treatment, switch_treatment, fcfg
        )

        # Write keyword file
        key_file = os.path.join(
            path_dir, f"key_{db_index}_{switch_treatment}.txt"
        )
        with open(key_file, "w") as f:
            f.write(key_text)
        print(f"  • Keyword file written -> {key_file}")

        # Call FVS
        try:
            call_fvs(path_dir, db_name, db_index, switch_treatment, cfg.exe("fvs"),
                     timeout=fcfg["run_timeout_s"])
            print(f"✓ FVS case {db_index} finished")
        except RuntimeError as exc:
            print(f"⚠ {exc}")  # continue or break, your choice


    print("\n🎉 All databases processed.")
