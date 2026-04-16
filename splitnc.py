import argparse
import codecs
import logging
from pathlib import Path
import re

import xarray as xr


def determine_field_vars(ds):
    reference_counts = {varname: 0 for varname in ds.variables}

    for varname in reference_counts.keys():
        for dim in ds[varname].dims:
            # Not all dimensions are variables
            if dim in reference_counts.keys():
                reference_counts[dim] += 1

        try:
            for coord in ds[varname].encoding["coordinates"].split(" "):
                reference_counts[coord] += 1
        except KeyError:
            pass

        try:
            reference_counts[ds[varname].attrs["bounds"]] += 1
        except KeyError:
            pass

    return sorted(
        [varname for varname, count in reference_counts.items() if count == 0]
    )


def get_dependent_vars(ds, varname, curr_vars=None):
    logging.debug(f"Determining dependent variables for {varname}")
    if curr_vars is None:
        curr_vars = set()

    # Get any dims that are also variables
    new_vars = {d for d in ds[varname].dims if d in ds.variables}

    # Get any coords
    if (
        "coordinates" in ds[varname].encoding
        and ds[varname].encoding["coordinates"] is not None
    ):
        new_vars.update(ds[varname].encoding["coordinates"].split(" "))

    # Add bounds if the variable has them
    if "bounds" in ds[varname].attrs:
        bounds = ds[varname].attrs["bounds"]
        new_vars.update([bounds])

    # Get the set of vars that are actually new
    diff_vars = new_vars.difference(curr_vars)

    all_vars = curr_vars | new_vars

    # Recurse on each new var
    additional_vars = set()
    for new_v in diff_vars:
        additional_vars |= get_dependent_vars(ds, new_v, all_vars)

    return diff_vars | additional_vars


def get_vars_in_order(ds, varname):
    # Order the variables
    vars_to_order = list(ds.variables)

    # Start with the field
    vars_in_order = [varname]
    vars_to_order.remove(varname)

    # Then the field's dimension and their bnds in order
    for dim_name in ds[varname].dims:
        if dim_name not in vars_to_order:
            continue

        vars_in_order.append(dim_name)
        vars_to_order.remove(dim_name)
        if "bounds" in ds[dim_name].attrs:
            dim_bnd_name = ds[dim_name].attrs["bounds"]
            if dim_bnd_name in vars_to_order:
                vars_in_order.append(dim_bnd_name)
                vars_to_order.remove(dim_bnd_name)

    # Then the remaining variables in alphabetical order
    vars_in_order += sorted(vars_to_order)

    return vars_in_order


def rename_variable(ds, oldname, newname):
    logging.debug(f"Renaming {oldname} to {newname}")
    ds_new = ds.rename({oldname: newname})

    try:
        old_bnd_name = ds_new[newname].attrs["bounds"]
        new_bnd_name = old_bnd_name.replace(oldname, newname)

        logging.debug(f"Renaming {old_bnd_name} to {new_bnd_name}")
        ds_new = ds_new.rename({old_bnd_name: new_bnd_name})

        # Update the attr on the original variable
        logging.debug(f'Updating "bounds" attr on {newname} to {new_bnd_name}')
        ds_new[newname].attrs["bounds"] = new_bnd_name
    except KeyError:
        # This variable doesn't have bounds
        pass

    return ds_new


def match_regex_list(regex_list, string_list):
    compiled_regex = [re.compile(regex) for regex in regex_list]
    return [s for s in string_list if any(r.fullmatch(s) for r in compiled_regex)]


def build_rename_dict(ds, rename_regex):
    logging.debug("Building rename dict")
    rename_dict = {}
    for coord in ds.coords:
        m = re.fullmatch(rename_regex, str(coord))

        if m:
            try:
                newname = m["newname"]
            except IndexError as e:
                logging.error(
                    f"{coord} matched regex for renaming, {rename_regex}, "
                    "but no \"newname\" capture group found"
                )
                raise e

            logging.debug(f"{coord} will be renamed to {newname}")

            rename_dict[coord] = newname

    return rename_dict


def process_file(
    filepath,
    field_vars=None,
    shared_vars=None,
    rename_regex=None,
    output_dir=None,
    overwrite=False,
):
    logging.debug(f"Processing {filepath}")
    filepath = Path(filepath)

    with xr.open_dataset(filepath, use_cftime=True) as ds:
        if field_vars is None or len(field_vars) == 0:
            logging.debug("Automatically determining field variables")

            field_vars = determine_field_vars(ds)

            # Shared vars shouldn't be field_vars
            if shared_vars:
                logging.debug("Removing shared variables from list of field variables")
                field_vars = [v for v in field_vars if v not in shared_vars]
        else:
            # There may be regex to process
            field_vars = match_regex_list(field_vars, ds.variables)
        logging.debug(f"List of field vars is: {field_vars}")

        # Resolve any regex in the shared_vars list
        if shared_vars:
            shared_vars = match_regex_list(shared_vars, ds.variables)
        else:
            shared_vars = []
        logging.debug(f"List of defined shared variables is: {shared_vars}")

        # Build the mapping dict for renaming, e.g. {"time_0: "time"}
        if rename_regex:
            rename_dict = build_rename_dict(ds, rename_regex)
        else:
            rename_dict = {}
        logging.debug(f"Rename dict is {rename_dict}")

        for v in field_vars:
            # Get the list of vars to keep for this field
            logging.debug(f"Determining dependent variables for field variable {v}")
            dependent_vars = get_dependent_vars(ds, v)
            full_var_list = [v] + list(dependent_vars) + shared_vars

            # Drop any vars not in the list
            drop_vars_list = [v for v in ds.variables if v not in full_var_list]
            ds_v = ds.drop_vars(drop_vars_list)

            # Rename anything in the rename dict
            if rename_dict:
                for old_name, new_name in rename_dict.items():
                    if (
                        old_name in ds_v.variables
                        or old_name in ds_v.dims
                        or old_name in ds_v.coords
                    ):
                        ds_v = rename_variable(ds_v, old_name, new_name)

            # Coordinates shouldn't have _FillValues
            for coord in list(ds_v.coords):
                if coord in ds_v.variables:
                    logging.debug(f'Setting "_FillValue" to None for {coord}')
                    ds_v[coord].encoding["_FillValue"] = None

            # Bounds shouldn't have coordinates or _FillValues
            bnds_set = {
                    ds_v[bnd_v].attrs["bounds"]
                    for bnd_v in ds_v.variables
                    if "bounds" in ds_v[bnd_v].attrs
            }
            logging.debug(f"Bounds variables are {bnds_set}")
            for bnd in bnds_set:
                logging.debug(
                    f'Setting "coordinates" and "_FillValue" to None for {bnd}'
                )
                ds_v[bnd].encoding["coordinates"] = None
                ds_v[bnd].encoding["_FillValue"] = None

            # Order the variables
            vars_in_order = get_vars_in_order(ds_v, v)
            logging.debug(f"Ordering variable as {vars_in_order}")
            ds_v = ds_v[vars_in_order]

            if output_dir:
                output_dir = Path(output_dir)
            else:
                output_dir = filepath.parent

            output_filename = output_dir / f"{v}_{filepath.name}"
            logging.debug(f"Output filepath is {output_filename}")

            if not overwrite and output_filename.exists():
                logging.error(f"Output file already exists - {output_filename}")
                logging.error("Use --overwrite to overwrite existing files")

                raise FileExistsError(f"{output_filename} already exists")

            logging.debug("Creating parent directory and writing to output file")
            output_filename.parent.mkdir(parents=True, exist_ok=True)
            ds_v.to_netcdf(output_filename)


#### Main
def arg_parse():
    parser = argparse.ArgumentParser(
        prog="splitnc",
        description="Splits a multi-field netCDF file into separate one-field files",
    )

    # Create a custom type for comma separated stings as lists
    def comma_separated_string_type(s):
        return s.split(",")

    # Escaped strings need some careful handling
    def unescaped_str(arg_str):
        return codecs.decode(str(arg_str), "unicode_escape")

    parser.add_argument("filepaths", nargs="+", help="One or more filepaths to process")
    parser.add_argument(
        "--field-vars",
        type=comma_separated_string_type,
        default=[],
        metavar="FIELD_VAR1,FIELD_VAR2,...",
        help="Specify the names of the field variables to split into separate "
            "files - dimensions, bounds, and coordinates of these fields will "
            "be included in each file. Disables automatic field variable "
            "identification. Regex patterns can be used here.",
    )
    parser.add_argument(
        "--shared-vars",
        type=comma_separated_string_type,
        default=[],
        metavar="SHARED_VAR1,SHARED_VAR2,...",
        help="Specify the names of variables that should be shared across "
            "files that cannot be automatically identified, as a comma "
            "separated list. Regex patterns can be used here.",
    )
    parser.add_argument(
        "--output-name-pattern",
        default="{field_var}_{filename}",
        help="The pattern to use for the names of output files. Use "
            "\"{field_var}\" for the name of the field variables, and "
            "\"{filename}\" for the original filename. Defaults to "
            "\"{field_var}_{filename}\".",
    )
    parser.add_argument(
        "--rename-regex",
        type=unescaped_str,
        metavar="REGEX",
        help="Look for duplicated coordinate names that match the given regex "
            "and rename them to the first \"newname\" capture group in the "
            "regex. E.g. \"(?P<newname>.*)_\\d+\" will match \"time_0\" and "
            "rename it to \"time\".",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for the processed files. If not given output "
            "files will be placed in the same directory as the original file.",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite existing files"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
    )

    return parser.parse_args()


def setup_logging(verbose=False):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="{asctime} - {levelname} - {message}",
        style="{",
        datefmt="%Y-%m-%d %H:%M",
    )


def main():
    args = arg_parse()

    setup_logging(args.verbose)

    logging.debug(f"Command line args are: {args}")

    for f in args.filepaths:
        process_file(
            f,
            field_vars=args.field_vars,
            shared_vars=args.shared_vars,
            rename_regex=args.rename_regex,
            output_dir=args.output_dir,
            overwrite=args.overwrite,
        )


if __name__ == "__main__":
    main()
