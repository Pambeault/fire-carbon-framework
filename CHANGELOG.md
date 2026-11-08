# Changelog

## v1.1.0 (2026-11-08)

Corrections made after the paper was first submitted, following a unit and
logic audit of the full pipeline. Results produced with v1.1.0 differ from
those produced with v1.0.0; the largest change comes from the FOFEM fuel units.

### FVS inputs and keywords
- **Slope in percent.** `VRI_processing` derives slope in degrees, but FVS
  expects percent. `VRI_to_FVS` now converts it (`tan(slope) × 100`).
- **SimFire percent area.** In the no-wildfire runs the percent-area field was
  misaligned, so FVS read a 100 % burn as 10 %. It is now written in a full
  10-column field.
- **Prescribed burn in both FVS runs.** FFE simulates only one fire per year, so
  with the prescribed burn and the wildfire both in 2025 the burn was lost. The
  prescribed burn is now in 2024, with `FlameAdj 0.3`, and is identical in the
  no-wildfire (`FVS_run`) and wildfire (`FVS_fire_run`) runs. The flame
  adjustment that was applied to the wildfire in treated stands was removed.
- **Wildfire fuel model.** FFE's automatic fuel model selection does not
  recognise BC habitat types and falls back to fuel model 8 for most stands, so
  the simulated wildfire was a low-intensity surface fire almost everywhere. The
  wildfire now uses each stand's FlamMap fuel model (`FUELMODL`; majority of the
  stand's pixels in the step-6 fuel model raster). Set with
  `fvs.wildfire_fuel_model` (`landscape` or `ffe`); the new pipeline step
  `landscape_fuel` writes the fuel model rasters before `fvs_fire_run`.
- **Crash stands.** Six stands added to `fvs.mortality_skip_stands`.
- `FVS_fire_run.main` accepts `chunks=[...]` to re-run selected input databases.

### FOFEM wildfire emissions
- **Fuel units.** `FVS_Fuels_Metric` reports surface fuels in Mg ha⁻¹ (tonnes per
  hectare), but they were passed to FOFEM as tons ac⁻¹, overstating fuel 2.24×.
  They are now converted (× 0.4461).
- **Percent of crown burned.** `PTorch_Sev` is a 0–1 probability; it is now
  multiplied by 100 before being passed to FOFEM.
- **Stands without live trees.** Their crown fuels were blank and FOFEM rejected
  them; crown fuels are now set to 0. `rerun_rejected()` re-runs only the stands
  FOFEM rejected for this reason.
- **Master files.** Each FOFEM master file collected the batch files of both
  treatment flags, so baseline and project emissions were mixed. Each flag now
  collects only its own batches, and repeated stands are dropped.
- **N2O.** The emission factor (0.16 g kg⁻¹, Akagi et al. 2011) is now divided by
  1000 for fuel consumption in tons, instead of by 10⁶.

### Prescribed-burn emissions (E_IFS)
- E_IFS is scaled by the treated area of each stand (`INTERSECT_HA`).
- The duff load is set to FOFEM's minimum (0.10 tons ac⁻¹), as the observed
  cultural burns do not consume duff. The previous load (FVS duff ÷ 100) fell
  below FOFEM's minimum for most stands, which FOFEM then rejected.

### Carbon stocks and carbon savings
- Carbon-stock rasters are in t CO2e ha⁻¹; their sums are now multiplied by the
  pixel area to give totals in Mg CO2e.
- The avoided-emissions table now also reports baseline and project wildfire
  emissions (`sum_W_baseline`, `sum_W_project`) and the mean relative burn
  probability factor (`mean_phi`).

### Other
- `fofem_to_raster` and `carbonsavings` can be re-run in the same Python session
  (per-flag in-memory raster names; existing outputs are overwritten).
- README: canopy raster scaling noted (m × 10, kg m⁻³ × 100).

## v1.0.0 (2026-11-03)

First public release.
