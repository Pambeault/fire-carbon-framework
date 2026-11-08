import pandas as pd
import math
import numpy as np
import sqlite3
import os


# Conversion factors#
acre_to_hect = 0.40468564
hect_to_acre = 1/0.40468564
meter2_to_hect = 0.0001
cm_to_inches = 0.39370079
meter_to_feet = 3.2808399
kgpm2_to_tons_per_acre = 3.98294251

# Set from config.yaml (fvs.*) in main()
_RNG = np.random.default_rng()
_PARAMS = {}

# ------------------------------------------------------------------ #
# 0. Utilities
# ------------------------------------------------------------------ #
def get_df_from_excel(file_path):
    return pd.read_excel(file_path, header=0, index_col=False)

def _iter_chunks(df: pd.DataFrame, chunk_size: int):
    """Yield (chunk_index, df_chunk)"""
    n_chunks = math.ceil(len(df) / chunk_size)
    for i in range(n_chunks):
        start = i * chunk_size
        yield i, df.iloc[start : start + chunk_size].copy()


def translate_species(df_vri):
    # VRI from: https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/forestry/stewardship/forest-analysis-inventory/data-management/standards/vegcomp_poly_rank1_data_dictionaryv5_2019.pdf
    # FVS from: Climate-FVS User's Guide (2014)
    transl_species_Dict = {
        'AC': 'AC',# Balsam Poplar -->plains cottonwood
        'ACT': 'AC',  # ACT - black cottonwood
        'AT': 'AT',  # AT - trembling aspen - none equivalent in USDA POTR5 = quaking aspen
        'FD': 'FD',   #Douglas-fir
        'FDI': 'FD',  # FDI - douglas-fir (interior) - multiple douglas-fir in USDA
        'JR': 'OH',  # JR - rocky mountain juniper (n)
        'PL': 'PL',  # PLI - lodgepole pine (interior)
        'PLI': 'PL',  # PLI - lodgepole pine (interior)
        'PY': 'PY',# Yellow pine --> pondoresa pine
        'S': 'S',# Spruce
        'SE': 'SE',# Engelmenn spruce
        'SX': 'S' }  # SX - spruce hybrid

    df_vri['SPECIES_CD_1'] = df_vri['SPECIES_CD_1'].map(transl_species_Dict)
    df_vri['SPECIES_CD_2'] = df_vri['SPECIES_CD_2'].map(transl_species_Dict)
    df_vri['SPECIES_CD_3'] = df_vri['SPECIES_CD_3'].map(transl_species_Dict)
    df_vri['SPECIES_CD_4'] = df_vri['SPECIES_CD_4'].map(transl_species_Dict)
    return df_vri

# Function not in use anymore. Multi-species not used anyways.
# def concatenate_species(row):
#     species_list = [row['SPECIES_CD_1'], row['SPECIES_CD_2'], row['SPECIES_CD_3'], row['SPECIES_CD_4']]
#     # Filter out NaN values
#     species_list = [species for species in species_list if pd.notna(species)]
#     # Join the species codes with a comma, up to a maximum of 6 species
#     return ', '.join(species_list[:4])

def dbh_to_height_species(species,size):

    if species is None or pd.isna(species):
        species = 'FD'

    transl_species_large_Dict = {
        'AC': [1.60,0.44],# Balsam Poplar -->plains cottonwood
        'AC': [1.60,0.44],  # ACT - black cottonwood
        'AT': [1.60,0.44],  # AT - trembling aspen - none equivalent in USDA POTR5 = quaking aspen
        'FD' : [1.95,0.52],   #Douglas-fir
        'FD' : [1.95,0.52],  # FDI - douglas-fir (interior) - multiple douglas-fir in USDA
        'OH': [1.60,0.44],  # JR - rocky mountain juniper (n)
        'PL' : [2.34,0.63],  # PLI - lodgepole pine (interior)
        'PL' : [2.34,0.63],  # PLI - lodgepole pine (interior)
        'PY' : [2.34,0.63],# Yellow pine --> pondoresa pine
        'S' : [1.95,0.52],# Spruce
        'SE' : [1.95,0.52],# Engelmenn spruce
        'S' : [1.95,0.52] }  # SX - spruce hybrid

    transl_species_small_Dict = {
        'AC': [0.76, 0.37],  # Balsam Poplar -->plains cottonwood
        'AC': [0.76, 0.37],  # ACT - black cottonwood
        'AT': [0.76, 0.37],  # AT - trembling aspen - none equivalent in USDA POTR5 = quaking aspen
        'FD': [1.56, 0.46],  # Douglas-fir
        'FD': [1.56, 0.46],  # FDI - douglas-fir (interior) - multiple douglas-fir in USDA
        'OH': [0.76, 0.37],  # JR - rocky mountain juniper (n)
        'PL': [2.00, 0.57],  # PLI - lodgepole pine (interior)
        'PL': [2.00, 0.57],  # PLI - lodgepole pine (interior)
        'PY': [2.00, 0.57],  # Yellow pine --> pondoresa pine
        'S': [1.56, 0.46],  # Spruce
        'SE': [1.56, 0.46],  # Engelmenn spruce
        'S': [1.56, 0.46]}  # SX - spruce hybrid

    # Select the correct dictionary based on size
    species_dict = transl_species_large_Dict if size == 'large' else transl_species_small_Dict

    # Get values, ensuring a valid two-element list
    param = species_dict.get(species, species_dict['FD'])

    return param


def height_to_cbh_species(species):

    if species is None or pd.isna(species):
        species = 'FD'

    transl_species_Dict = {
        'AC': [0.46, -0.08],# Balsam Poplar -->plains cottonwood
        'AC': [0.46, -0.08],  # ACT - black cottonwood
        'AT': [0.46, -0.08],  # AT - trembling aspen - none equivalent in USDA POTR5 = quaking aspen
        'FD' : [0.33,0.1],   #Douglas-fir
        'OH': [0.46, -0.08],  # JR - rocky mountain juniper (n)
        'PL' : [0.5, -1.29],  # PLI - lodgepole pine (interior)
        'PY' : [0.5, -1.29],# Yellow pine --> pondoresa pine
        'SE' : [0.33,0.1],# Engelmenn spruce
        'S' : [0.33,0.1] }  # SX - spruce hybrid

    param = transl_species_Dict.get(species)
    return param


def fvs_standinit(df_vri, fcfg):


    # Go one variable at a time
    df_stand_init = pd.DataFrame()
    df_stand_init['STAND_ID'] = df_vri['POLYGON_ID']        # Need to remove spaces
    df_stand_init['VARIANT'] = fcfg['variant']              # BC variant
    df_stand_init['INV_YEAR'] = fcfg['inventory_year']      # required field
    df_stand_init['GROUPS'] = np.nan                        # Not required
    df_stand_init['ADDFILES'] = np.nan                      # Not required
    df_stand_init['FVSKEYWORDS'] = np.nan                   # Not required
    df_stand_init['GIS_LINK'] = np.nan                      # Not required
    df_stand_init['PROJECT_NAME'] = np.nan                  # Not required
    df_stand_init['LATITUDE'] = df_vri['Y']                 # Position of suggested label - this is an approximation
    df_stand_init['LONGITUDE'] = df_vri['X']                # Position of suggested label - this is an approximation
    df_stand_init['REGION'] = np.nan                        # Not required if "LOCATION" IS INPUTED
    df_stand_init['FOREST'] = np.nan                        # Not required if "LOCATION" IS INPUTED
    df_stand_init['DISTRICT'] = np.nan                      # Not required if "LOCATION" IS INPUTED
    df_stand_init['COMPARTMENT'] = np.nan                   # Not required if "LOCATION" IS INPUTED
    df_stand_init['LOCATION'] = np.nan                      # Found in Variant overview - !!!NEED to find for BC variant
    df_stand_init['ECOREGION'] = fcfg['ecoregion']                     # Not required - used in Southern 'VARIANT's
    df_stand_init['PV_CODE'] = np.nan                       # Found in Variant overview - appendix B - best guess for lodgepole pine
    df_stand_init['PV_REF_CODE'] = np.nan                   # Not required
    df_stand_init['AGE'] = np.nan # OLD = df_vri['PROJ_AGE_1'] * df_vri['SPECIES_PCT_1']/(df_vri['SPECIES_PCT_1'] + df_vri['SPECIES_PCT_2']) + df_vri['PROJ_AGE_2'] * df_vri['SPECIES_PCT_2']/(df_vri['SPECIES_PCT_1'] + df_vri['SPECIES_PCT_2'])
    df_stand_init['ASPECT'] = df_vri['aspect'].clip(lower=0, upper = 360)                     # !!!Need to create a column here['plot_aspect']     # UNITS = degrees
    df_stand_init['SLOPE'] = np.tan(np.radians(df_vri['slope'])) * 100   # VRI_processing gives degrees; FVS expects percent
    df_stand_init['ELEVATION'] = df_vri['elev']           # Not required if 'ELEVFT' is entered
    df_stand_init['ELEVFT'] = np.nan
    df_stand_init['BASAL_AREA_FACTOR'] = -1   # UNITS 1/area in hectare - for large tree plot
    df_stand_init['INV_PLOT_SIZE'] = 1        # UNITS 1/hectare plots
    df_stand_init['BRK_DBH'] = fcfg['breakpoint_dbh_cm']          # UNITS = cm - Breakpoint diameter between small and large tree
    df_stand_init['NUM_PLOTS'] = 1              # Number of plots - processed during the join of the different plot
    df_stand_init['NONSTK_PLOTS'] = np.nan                  # Not required - non stockable plots
    df_stand_init['SAM_WT'] = 1/df_vri['ADJUSTED_AREA_FACTOR']                            # multiplier for area
    df_stand_init['STK_PCNT'] = np.nan                      # Not required
    df_stand_init['DG_TRANS'] = np.nan                      # Not required - Diameter taken at the end of growth period. See metadata for other options
    df_stand_init['DG_MEASURE'] = np.nan                    # Not required - UNITS = years - diameter growth measurement method
    df_stand_init['HTG_TRANS'] = np.nan                     # Not required - Same as above, but for height
    df_stand_init['HTG_MEASURE'] = np.nan                   # Not required - Same as above
    df_stand_init['MORT_MEASURE'] = np.nan                  # Not required - UNITS = years - Mortality measurement period
    df_stand_init['MAX_BA'] = np.nan                        # Not required - maximum basal area
    df_stand_init['MAX_SDI'] = np.nan                       # Not required - Refer to Variant overview for value
    df_stand_init['SITE_SPECIES'] = df_vri['SPECIES_CD_1']  # Multi species says it's not reconized. Only first species then
    df_stand_init['SITE_INDEX'] = df_vri['SITE_INDEX']      # Not required - Refer to Variant overview
    df_stand_init['MODEL_TYPE'] = np.nan                    # For CR variant only
    df_stand_init['PHYSIO_REGION'] = np.nan                 # Not required
    df_stand_init['FOREST_TYPE'] = np.nan                   # Not required
    df_stand_init['STATE'] = np.nan                         # Not required
    df_stand_init['COUNTY'] = np.nan                       # Not required
    df_stand_init['FUEL_MODEL'] = np.nan                    # Can select your model. Blank will select the best one
    # Fuel does not impact carbon tables as it is based on stockable area
    df_stand_init['FUEL_0_25_H'] = np.nan
    df_stand_init['FUEL_25_1_H'] = np.nan
    df_stand_init['FUEL_1_3_H'] = np.nan
    df_stand_init['FUEL_3_6_H'] = np.nan
    df_stand_init['FUEL_6_12_H'] = np.nan
    df_stand_init['FUEL_12_20_H'] = np.nan
    df_stand_init['FUEL_20_35_H'] = np.nan
    df_stand_init['FUEL_35_50_H'] = np.nan
    df_stand_init['FUEL_GT_50_H'] = np.nan
    df_stand_init['FUEL_0_25_S'] = np.nan
    df_stand_init['FUEL_25_1_S'] = np.nan
    df_stand_init['FUEL_1_3_S'] = np.nan
    df_stand_init['FUEL_3_6_S'] = np.nan
    df_stand_init['FUEL_6_12_S'] = np.nan
    df_stand_init['FUEL_12_20_S'] = np.nan
    df_stand_init['FUEL_20_35_S'] = np.nan
    df_stand_init['FUEL_35_50_S'] = np.nan
    df_stand_init['FUEL_GT_50_S'] = np.nan
    df_stand_init['FUEL_Litter'] = np.nan
    df_stand_init['FUEL_Duff'] = np.nan
    df_stand_init['FUEL_0_06_H'] = np.nan
    df_stand_init['FUEL_06_25_H'] = np.nan
    df_stand_init['FUEL_25_76_H'] = np.nan
    df_stand_init['FUEL_76_152_H'] = np.nan
    df_stand_init['FUEL_152_305_H'] = np.nan
    df_stand_init['FUEL_305_508_H'] = np.nan
    df_stand_init['FUEL_508_889_H'] = np.nan
    df_stand_init['FUEL_889_1270_H'] = np.nan
    df_stand_init['FUEL_gt_1270_H'] = np.nan
    df_stand_init['FUEL_0_06_S'] = np.nan
    df_stand_init['FUEL_06_25_S'] = np.nan
    df_stand_init['FUEL_25_76_S'] = np.nan
    df_stand_init['FUEL_76_152_S'] = np.nan
    df_stand_init['FUEL_152_305_S'] = np.nan
    df_stand_init['FUEL_305_508_S'] = np.nan
    df_stand_init['FUEL_508_889_S'] = np.nan
    df_stand_init['FUEL_889_1270_S'] = np.nan
    df_stand_init['FUEL_gt_1270_S'] = np.nan
    df_stand_init['PHOTO_REF'] = np.nan                        # Not required
    df_stand_init['PHOTO_CODE'] = np.nan                       # Not required
    df_stand_init['MOITURE'] = np.nan

    return df_stand_init


def generate_tree_list(row, species, species_pct):
    """
    Generate a synthetic tree list for a single species within a stand.

    Height is drawn from a lognormal distribution anchored to VRI PROJ_HEIGHT_1,
    with sigma derived from empirical within-stand CV of 0.56. DBH is derived
    from height using species-specific multipliers for small and large trees.

    Parameters
    ----------
    row : pd.Series
        One row from the VRI dataframe, containing stand-level attributes.
    species : str
        FVS species code (e.g. 'PL', 'AT', 'FD').
    species_pct : float
        Percentage of stand basal area attributed to this species.

    Returns
    -------
    pd.DataFrame
        Tree-level records ready for conversion to FVS_TreeInit format,
        or an empty DataFrame if no trees are generated.
    """

    # ------------------------------------------------------------------
    # 0. Guard: valid species
    # ------------------------------------------------------------------
    if species is None or pd.isna(species):
        species = 'FD'

    # ------------------------------------------------------------------
    # 1. Tree counts
    # ------------------------------------------------------------------
    live_tree = row['VRI_LIVE_STEMS_PER_HA'] / row['ADJUSTED_AREA_FACTOR'] * species_pct / 100
    live_tree = int(round(live_tree))
    dead_tree = row['VRI_DEAD_STEMS_PER_HA'] / row['ADJUSTED_AREA_FACTOR'] * species_pct / 100
    dead_tree = int(round(dead_tree))
    total_tree = live_tree + dead_tree

    if total_tree == 0:
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 2. Height generation (lognormal, anchored to VRI mean)
    # ------------------------------------------------------------------
    target_mean_ht = row['PROJ_HEIGHT_1']
    target_stddev_ht = target_mean_ht * _PARAMS['height_cv']   # empirical within-stand CV

    mu_ht = math.log(
        (target_mean_ht ** 2) /
        math.sqrt(target_stddev_ht ** 2 + target_mean_ht ** 2)
    )
    sigma_ht = math.sqrt(
        math.log(1 + (target_stddev_ht ** 2) / (target_mean_ht ** 2))
    )

    rng = _RNG   # seeded from fvs.random_seed (null = unseeded)
    tree_list = pd.DataFrame()
    tree_list['Height'] = rng.lognormal(mu_ht, sigma_ht, total_tree)
    tree_list['Height'] = np.where(tree_list['Height'] < 1.3, 1.3, tree_list['Height'])
    tree_list['Height'] = np.where(tree_list['Height'] > 120, 120, tree_list['Height'])

    # ------------------------------------------------------------------
    # 3. DBH generation from height via species-specific multipliers
    # ------------------------------------------------------------------
    param_small = dbh_to_height_species(species, 'small')
    multiplicator_small = rng.normal(param_small[0], param_small[1], total_tree)
    while np.any(multiplicator_small <= 0):
        multiplicator_small[multiplicator_small <= 0] = rng.normal(
            param_small[0], param_small[1], np.sum(multiplicator_small <= 0)
        )

    tree_list['dbh_small'] = tree_list['Height'] * multiplicator_small

    param_large = dbh_to_height_species(species, 'large')
    multiplicator_large = rng.normal(param_large[0], param_large[1], total_tree)
    while np.any(multiplicator_large <= 0):
        mask = multiplicator_large <= 0
        multiplicator_large[mask] = rng.normal(
            param_large[0], param_large[1], mask.sum()
        )

    tree_list['dbh_large'] = tree_list['Height'] * multiplicator_large

    # ------------------------------------------------------------------
    # 4. Size classification and DBH assignment
    # ------------------------------------------------------------------
    threshold = _PARAMS['breakpoint_dbh_cm']
    tree_list['size'] = tree_list['dbh_large'].apply(
        lambda x: 'small' if x < threshold else 'large'
    )
    tree_list['dbh'] = np.where(
        tree_list['size'] == 'small',
        tree_list['dbh_small'],
        tree_list['dbh_large']
    )
    tree_list['dbh'] = tree_list['dbh'].clip(lower=2, upper=100)

    # ------------------------------------------------------------------
    # 5. Diagnostic: log implausible H/D ratios without correcting them
    # ------------------------------------------------------------------
    hd_actual = tree_list['Height'] / tree_list['dbh']
    # n_implausible = (hd_actual > 1.5).sum()
    # if n_implausible > 0:
    #     print(f"  ⚠ Stand {row['POLYGON_ID']} ({species}): "
    #           f"{n_implausible}/{total_tree} trees with H/D > 1.5 "
    #           f"(max H/D = {hd_actual.max():.2f})")

    # ------------------------------------------------------------------
    # 6. Crown base height
    # ------------------------------------------------------------------
    param = height_to_cbh_species(species)
    tree_list['height_to_live_crown'] = (
        param[0] * tree_list['Height'] + param[1]
    )
    tree_list['height_to_live_crown'] = np.maximum(
        0,
        np.minimum(tree_list['height_to_live_crown'], tree_list['Height'])
    )

    # ------------------------------------------------------------------
    # 7. Decay, damage, identifiers
    # ------------------------------------------------------------------
    tree_list['decay'] = [9] * dead_tree + [1] * live_tree
    tree_list['DAMAGE1'] = np.nan
    tree_list['HTTOPK'] = np.nan
    tree_list.loc[:, 'stand'] = row['POLYGON_ID']
    tree_list.loc[:, 'species'] = species

    return tree_list


def fvs_treeinit(df_tree_list):
    df_tree_init = pd.DataFrame()
    df_tree_init['STAND_ID'] = df_tree_list['stand']
    df_tree_init['PLOT_ID'] = 1
    df_tree_init['STANDPLOT_ID'] = df_tree_init['STAND_ID'].astype(str) + '_' + df_tree_init['PLOT_ID'].astype(str)
    df_tree_init['TREE_ID'] = range(1, len(df_tree_init) + 1)
    df_tree_init['TAG_ID'] = np.nan                         # Not required
    df_tree_init['TREE_COUNT'] = 1                          # Different than 1 only if each line represents more than 1 tree
    df_tree_init['HISTORY'] =  df_tree_list['decay']        # 1 for live and 9 for dead - unsure intermediate, but good for now
    df_tree_init['SPECIES'] = df_tree_list['species']       # Need to use USDA plant code, FVS 2 letter acronym or FIA species code
    df_tree_init['DIAMETER'] = df_tree_list['dbh']          # Units are in metric for BC variant. For trees taller than 4.5ft - we take 130cm
    df_tree_init['DIAMETER_HT'] = np.nan                    # Not required
    df_tree_init['DG'] = np.nan                             # Units = inches . Not required. Periodic diameter increment.
    df_tree_init['HT'] = df_tree_list['Height']             # Units are in metric for BC variant
    df_tree_init['HTG'] = np.nan                            # Units = feet . Not required. Periodic height increment
    df_tree_init['HTTOPK'] = df_tree_list['HTTOPK']         # Height of top kill . Usefull only if recorded
    df_tree_init['HT_TO_LIVE_CROWN'] = df_tree_list['height_to_live_crown']                 #
    df_tree_init['CRRATIO'] = np.round(((df_tree_init['HT'] - df_tree_init['HT_TO_LIVE_CROWN']) /  df_tree_init['HT']) *100)      #
    df_tree_init['DAMAGE1'] = df_tree_list['DAMAGE1']    # !!!Validate if the codes are the same
    df_tree_init['SEVERITY1'] = np.nan                      # !!!VALIDATE
    df_tree_init['DAMAGE2'] = np.nan                        # !!!in data: discoloration / crown consumed / tree bk char / tree basal char / tree root damage
    df_tree_init['SEVERITY2'] = np.nan                      # !!!VALIDATE
    df_tree_init['DAMAGE3'] = np.nan                        # !!!VALIDATE
    df_tree_init['SEVERITY3'] = np.nan                      # !!!VALIDATE
    df_tree_init['DEFECT_CUBIC'] = np.nan                   # not applicable
    df_tree_init['DEFECT_BOARD'] = np.nan                   # not applicable
    df_tree_init['TREEVALUE'] = np.nan                      # ???Is required??? 1= desirable timber, 2= acceptable, 3 =undesirable
    df_tree_init['PRESCRIPTION'] = np.nan                   # Used if thinning prescriptions used in FVS
    df_tree_init['AGE'] = np.nan                            # ??? sufficient to have in stand?Have only a portion. Is likely enough
    df_tree_init['SLOPE'] = np.nan                          # Not required since it's entered in StandInit
    df_tree_init['ASPECT'] = np.nan                         # Not required since it's entered in StandInit
    df_tree_init['PV_CODE'] = np.nan                        # Not required since it's entered in StandInit
    df_tree_init['PV_REF_CODE'] = np.nan                    # Not required since it's entered in StandInit
    df_tree_init['TOPOCODE'] = np.nan                       # 1=bottom, 2=lower, 3=mid slope, 4=upper slope, and 5=ridge top
    df_tree_init['SITEPREP'] = np.nan                       # 1=none, 2=mechanical, 3=burn, and 4=road cuts/road fills/stockable road beds

    return df_tree_init

def get_max_trees(live_stems_per_ha):
    """
    Tiered cap based on stand density.
    Dense young stands need more trees to stabilize basal area estimates
    when each tree carries high expansion weight.
    """
    # config: fvs.max_trees_tiers = [[upper bound, max trees], ..., [null, max trees]]
    for upper, max_trees in _PARAMS["max_trees_tiers"]:
        if upper is None or live_stems_per_ha < upper:
            return max_trees

def main(cfg, year):
    """
        Process a large VRI → FVS conversion in chunks of *STANDS_PER_DB* stands.
        Each chunk gets its own Excel and SQLite file:
            <folder>/FVS_partNN.xlsx
            <folder>/FVS_partNN.sqlite
        """
    global _RNG, _PARAMS
    fcfg = cfg["fvs"]
    _PARAMS = fcfg
    _RNG = np.random.default_rng(fcfg.get("random_seed"))
    path_VRI = cfg.path("processed_dir", f"VRI_{year}_processed.xlsx")
    path_out_folder = cfg.path("fvs_dir", year=year)
    os.makedirs(path_out_folder, exist_ok=True)

    # 0) Load & filter VRI -------------------------------------------------
    df_vri = get_df_from_excel(path_VRI)
    df_vri["SPECIES_CD_1"] = df_vri["SPECIES_CD_1"].fillna("FD")

    # 1) Pre-calculate adjusted area factor -------------------------------
    df_vri["VRI_LIVE_STEMS_PER_HA"] = df_vri["VRI_LIVE_STEMS_PER_HA"].fillna(0)
    df_vri["VRI_DEAD_STEMS_PER_HA"] = df_vri["VRI_DEAD_STEMS_PER_HA"].fillna(0)
    df_vri["ADJUSTED_AREA_FACTOR"] = (
        np.ceil(
            (df_vri["VRI_LIVE_STEMS_PER_HA"] + df_vri["VRI_DEAD_STEMS_PER_HA"])
            / df_vri["VRI_LIVE_STEMS_PER_HA"].apply(get_max_trees)
        )
        .clip(lower=1)
        .astype(int)
    )

    # 2) Translate species codes once
    df_vri = translate_species(df_vri)

    # --------------------------------------------------------------------
    # 3) Chunk loop
    # --------------------------------------------------------------------
    STANDS_PER_DB = fcfg["stands_per_db"]  # rows per chunk

    for idx, df_chunk in _iter_chunks(df_vri, STANDS_PER_DB):
        print(f"=== Processing chunk {idx} (rows {len(df_chunk)}) ===")

        # 3.1 StandInit from the chunk
        df_stand_init = fvs_standinit(df_chunk, fcfg)

        # 3.2 TreeInit accumulator for this chunk
        df_tree_init = pd.DataFrame()

        # 3.3 Row-wise loop for tree lists
        for _, row in df_chunk.iterrows():
            df_tree_list = pd.DataFrame()

            for i in range(1, 5):
                species = row[f"SPECIES_CD_{i}"]
                species_pct = row[f"SPECIES_PCT_{i}"]
                if species_pct > 0 and pd.notna(species):
                    tl = generate_tree_list(row, species, species_pct)
                    if not tl.empty:
                        df_tree_list = pd.concat([df_tree_list, tl],
                                                 ignore_index=True)

            # Convert each stand’s tree list to FVS tree_init rows
            if not df_tree_list.empty:
                tinit = fvs_treeinit(df_tree_list)
                df_tree_init = pd.concat([df_tree_init, tinit],
                                         ignore_index=True)

        # ----------------------------------------------------------------
        # 4) Write this chunk out
        # ----------------------------------------------------------------
        base_name = f"FVS_in_{idx:02d}"
        excel_path = os.path.join(path_out_folder, base_name + ".xlsx")
        db_path = os.path.join(path_out_folder, base_name + ".db")

        # Excel
        with pd.ExcelWriter(excel_path, engine="openpyxl") as xw:
            df_stand_init.to_excel(xw, sheet_name="FVS_StandInit", index=False)
            df_tree_init.to_excel(xw, sheet_name="FVS_TreeInit", index=False)
        print(f"  • Excel written → {excel_path}")

        # SQLite
        conn = sqlite3.connect(db_path)
        df_stand_init.to_sql("FVS_StandInit", conn, if_exists="replace", index=False)
        df_tree_init.to_sql("FVS_TreeInit", conn, if_exists="replace", index=False)
        conn.close()
        print(f"  • SQLite written → {db_path}")

    print("🎉 All chunks processed.")
