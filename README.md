# splitnc
A simple script that splits multi-field netCDF files into single-field files

### Example Usage

#### Atmosphere
To use this script for split multi-field atmosphere files from ACCESS-ESM1.6:
```bash
python split-nc.py --shared-vars latitude_longitude  --rename-regex "(?P<newname>.*)_\\d+" $INPUT_DIR/*.nc
```

`splitnc` will automatically determine which variables are fields by looking at which variables depend on other variables. Variables with nothing depending on them are deemed to be fields.
Alternatively one can use `--field-vars fld_.*` to match the variable names in these files.

The `--rename-regex` option with the supplied regex will rename variables like
`time_0` or `pseudo_level_0` are renamed to `time` or `pseudo_level`.

The `--shared-vars` option will ensure that the variable `latitude_longitude` is
included in all files even though none of the field variable depend on it.

#### Ice
To use this script for split multi-field ice files from ACCESS-ESM1.6:
```bash
python split-nc.py --shared-vars uarea,tmask,tarea  $INPUT_DIR/*.nc
```

With ice files the shared-vars are different and there are no duplicated variables that require renaming.
