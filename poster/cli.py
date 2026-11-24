"""Command-line entry point: argument parsing and the poster / 3-D run."""

import argparse
import json
import os
import sys
from font_management import load_fonts
from lat_lon_parser import parse
from .build3d import build_scene_3d, create_poster_3d
from .osm import get_coordinates
from .render2d import create_poster
from .settings import DEFAULT_DISTANCE_2D, DEFAULT_DISTANCE_3D, FILE_ENCODING, THEMES_DIR, THREE_D_OUTPUTS
from .themes import generate_output_filename, get_available_themes, load_theme, set_active_theme


def print_examples():
    """Print usage examples."""
    print("""
City Map Poster Generator
=========================

Usage:
  python create_map_poster.py --city <city> --country <country> [options]

Examples:
  # Iconic grid patterns
  python create_map_poster.py -c "New York" -C "USA" -t noir -d 12000           # Manhattan grid
  python create_map_poster.py -c "Barcelona" -C "Spain" -t warm_beige -d 8000   # Eixample district grid

  # Waterfront & canals
  python create_map_poster.py -c "Venice" -C "Italy" -t blueprint -d 4000       # Canal network
  python create_map_poster.py -c "Amsterdam" -C "Netherlands" -t ocean -d 6000  # Concentric canals
  python create_map_poster.py -c "Dubai" -C "UAE" -t midnight_blue -d 15000     # Palm & coastline

  # Radial patterns
  python create_map_poster.py -c "Paris" -C "France" -t pastel_dream -d 10000   # Haussmann boulevards
  python create_map_poster.py -c "Moscow" -C "Russia" -t noir -d 12000          # Ring roads

  # Organic old cities
  python create_map_poster.py -c "Tokyo" -C "Japan" -t japanese_ink -d 15000    # Dense organic streets
  python create_map_poster.py -c "Marrakech" -C "Morocco" -t terracotta -d 5000 # Medina maze
  python create_map_poster.py -c "Rome" -C "Italy" -t warm_beige -d 8000        # Ancient street layout

  # Coastal cities
  python create_map_poster.py -c "San Francisco" -C "USA" -t sunset -d 10000    # Peninsula grid
  python create_map_poster.py -c "Sydney" -C "Australia" -t ocean -d 12000      # Harbor city
  python create_map_poster.py -c "Mumbai" -C "India" -t contrast_zones -d 18000 # Coastal peninsula

  # River cities
  python create_map_poster.py -c "London" -C "UK" -t noir -d 15000              # Thames curves
  python create_map_poster.py -c "Budapest" -C "Hungary" -t copper_patina -d 8000  # Danube split

  # List themes
  python create_map_poster.py --list-themes

Options:
  --city, -c        City name (required)
  --country, -C     Country name (required)
  --country-label   Override country text displayed on poster
  --theme, -t       Theme name (default: terracotta)
  --all-themes      Generate posters for all themes
  --distance, -d    Map radius in meters (default: 18000)
  --list-themes     List all available themes

Distance guide:
  4000-6000m   Small/dense cities (Venice, Amsterdam old center)
  8000-12000m  Medium cities, focused downtown (Paris, Barcelona)
  15000-20000m Large metros, full city view (Tokyo, Mumbai)

Available themes can be found in the 'themes/' directory.
Generated posters are saved to 'posters/' directory.
""")


def list_themes():
    """List all available themes with descriptions."""
    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        return

    print("\nAvailable Themes:")
    print("-" * 60)
    for theme_name in available_themes:
        theme_path = os.path.join(THEMES_DIR, f"{theme_name}.json")
        try:
            with open(theme_path, "r", encoding=FILE_ENCODING) as f:
                theme_data = json.load(f)
                display_name = theme_data.get('name', theme_name)
                description = theme_data.get('description', '')
        except (OSError, json.JSONDecodeError):
            display_name = theme_name
            description = ""
        print(f"  {theme_name}")
        print(f"    {display_name}")
        if description:
            print(f"    {description}")
        print()
def main():


    parser = argparse.ArgumentParser(
        description="Generate beautiful map posters for any city",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python create_map_poster.py --city "New York" --country "USA"
  python create_map_poster.py --city "New York" --country "USA" -l 40.776676 -73.971321 --theme neon_cyberpunk
  python create_map_poster.py --city Tokyo --country Japan --theme midnight_blue
  python create_map_poster.py --city Paris --country France --theme noir --distance 15000
  python create_map_poster.py --list-themes
        """,
    )

    parser.add_argument("--city", "-c", type=str, help="City name")
    parser.add_argument("--country", "-C", type=str, help="Country name")
    parser.add_argument(
        "--latitude",
        "-lat",
        dest="latitude",
        type=str,
        help="Override latitude center point",
    )
    parser.add_argument(
        "--longitude",
        "-long",
        dest="longitude",
        type=str,
        help="Override longitude center point",
    )
    parser.add_argument(
        "--country-label",
        dest="country_label",
        type=str,
        help="Override country text displayed on poster",
    )
    parser.add_argument(
        "--theme",
        "-t",
        type=str,
        default="terracotta",
        help="Theme name (default: terracotta)",
    )
    parser.add_argument(
        "--all-themes",
        "--All-themes",
        dest="all_themes",
        action="store_true",
        help="Generate posters for all themes",
    )
    parser.add_argument(
        "--distance",
        "-d",
        type=int,
        default=None,
        help=f"Map radius in meters (default: {DEFAULT_DISTANCE_2D}, or {DEFAULT_DISTANCE_3D} with --3d)",
    )
    parser.add_argument(
        "--width",
        "-W",
        type=float,
        default=12,
        help="Image width in inches (default: 12, max: 20 )",
    )
    parser.add_argument(
        "--height",
        "-H",
        type=float,
        default=16,
        help="Image height in inches (default: 16, max: 20)",
    )
    parser.add_argument(
        "--list-themes", action="store_true", help="List all available themes"
    )
    parser.add_argument(
        "--display-city",
        "-dc",
        type=str,
        help="Custom display name for city (for i18n support)",
    )
    parser.add_argument(
        "--display-country",
        "-dC",
        type=str,
        help="Custom display name for country (for i18n support)",
    )
    parser.add_argument(
        "--font-family",
        type=str,
        help='Google Fonts family name (e.g., "Noto Sans JP", "Open Sans"). If not specified, uses local Roboto fonts.',
    )
    parser.add_argument(
        "--format",
        "-f",
        default="png",
        choices=["png", "svg", "pdf"],
        help="Output format for the poster (default: png)",
    )
    parser.add_argument(
        "--no-railways",
        dest="railways",
        action="store_false",
        help="Disable the railway line layer (enabled by default)",
    )
    parser.add_argument(
        "--no-buildings",
        dest="buildings",
        action="store_false",
        help="Disable the building footprint layer (enabled by default)",
    )
    parser.add_argument(
        "--no-labels",
        dest="labels",
        action="store_false",
        help="Disable the city/country title text (useful for tiled/panorama panels)",
    )
    parser.add_argument(
        "--no-gradient",
        dest="gradient",
        action="store_false",
        help="Disable the top/bottom vignette fade (useful for tiled/panorama panels, "
             "where each panel's fade would otherwise double up at the seams)",
    )
    parser.add_argument(
        "--3d",
        dest="three_d",
        action="store_true",
        help="Build a 3D city model (terrain, extruded buildings, trees, bridges) instead of a flat poster",
    )
    parser.add_argument(
        "--3d-outputs",
        dest="outputs",
        type=str,
        default="poster,db",
        help="With --3d: comma-separated outputs from poster (rendered image), db (baked map: the viewer data and "
             "the exports in one file, opened by the GUI), html (standalone interactive page), glb (colored 3D model), "
             "stl (3D-printing geometry). Default: poster,db",
    )
    parser.add_argument(
        "--azimuth",
        type=float,
        default=225.0,
        help="With --3d: compass bearing of the camera in degrees; 180 looks north from the south (default: 225)",
    )
    parser.add_argument(
        "--elevation",
        type=float,
        default=45.0,
        help="With --3d: camera height angle in degrees, 90 is straight down (default: 45)",
    )
    parser.add_argument(
        "--exaggeration",
        type=float,
        default=1.5,
        help="With --3d: vertical exaggeration of the terrain (default: 1.5)",
    )
    parser.add_argument(
        "--building-exaggeration",
        type=float,
        default=None,
        help="With --3d: vertical exaggeration of buildings and trees (default: same as --exaggeration)",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        default=1.0,
        help="With --3d: >1 crops in on the model in the rendered poster (default: 1)",
    )
    parser.add_argument(
        "--print-size",
        dest="print_size",
        type=float,
        default=200.0,
        help="With --3d and the stl output: width of the printed model in millimetres (default: 200)",
    )
    parser.add_argument(
        "--tiles",
        type=int,
        default=1,
        help="With --3d: build a TILES x TILES grid, each tile --distance metres from centre to edge, so a larger "
             "area stays detailed. The viewer loads tiles on demand and simplifies distant ones (default: 1)",
    )
    parser.add_argument(
        "--no-trees",
        dest="trees",
        action="store_false",
        help="With --3d: leave out trees",
    )
    parser.add_argument(
        "--no-traffic",
        dest="traffic",
        action="store_false",
        help="With --3d: leave out the animated cars that stop at traffic signals and stop signs",
    )
    parser.add_argument(
        "--no-terrain",
        dest="terrain",
        action="store_false",
        help="With --3d: flat ground instead of real elevation",
    )
    parser.add_argument(
        "--no-overture",
        dest="overture",
        action="store_false",
        help="With --3d: use OpenStreetMap only, skip Overture Maps footprints/heights/land cover",
    )
    parser.add_argument(
        "--no-underground",
        dest="underground",
        action="store_false",
        help="With --3d: skip the geology, mines, drill holes and mining claims shown in the underground cutaway",
    )
    parser.set_defaults(railways=True, buildings=True, labels=True, gradient=True, trees=True, terrain=True,
                        overture=True, traffic=True, underground=True)

    args = parser.parse_args()

    # If no arguments provided, show examples
    if len(sys.argv) == 1:
        print_examples()
        sys.exit(0)

    # List themes if requested
    if args.list_themes:
        list_themes()
        sys.exit(0)

    # Validate required arguments
    if not args.city or not args.country:
        print("Error: --city and --country are required.\n")
        print_examples()
        sys.exit(1)

    # Enforce maximum dimensions
    if args.width > 20:
        print(
            f"⚠ Width {args.width} exceeds the maximum allowed limit of 20. It's enforced as max limit 20."
        )
        args.width = 20.0
    if args.height > 20:
        print(
            f"⚠ Height {args.height} exceeds the maximum allowed limit of 20. It's enforced as max limit 20."
        )
        args.height = 20.0

    outputs_3d = [o.strip().lower() for o in args.outputs.split(",") if o.strip()]
    if args.three_d:
        unknown = [o for o in outputs_3d if o not in THREE_D_OUTPUTS]
        if unknown or not outputs_3d:
            print(f"Error: unknown --3d-outputs {unknown or args.outputs!r}. Choose from: {', '.join(THREE_D_OUTPUTS)}")
            sys.exit(1)
        if not 5 <= args.elevation <= 90:
            print("Error: --elevation must be between 5 and 90 degrees.")
            sys.exit(1)
        if not 0.1 <= args.exaggeration <= 8:
            print("Error: --exaggeration must be between 0.1 and 8.")
            sys.exit(1)
        if args.building_exaggeration is not None and not 0.1 <= args.building_exaggeration <= 8:
            print("Error: --building-exaggeration must be between 0.1 and 8.")
            sys.exit(1)
        if args.zoom <= 0:
            print("Error: --zoom must be positive.")
            sys.exit(1)
        if not 1 <= args.tiles <= 7:
            print("Error: --tiles must be between 1 and 7.")
            sys.exit(1)
    if args.distance is None:
        args.distance = DEFAULT_DISTANCE_3D if args.three_d else DEFAULT_DISTANCE_2D
    if args.three_d and args.tiles > 3:
        print(f"⚠ {args.tiles} x {args.tiles} tiles is a very large model "
              f"({args.tiles * args.distance * 2 / 1000:.0f} km across): expect long builds and big files; "
              "the poster image is the slowest part, so consider --3d-outputs html.")
    if args.three_d and args.distance > 4000:
        print(f"⚠ A {args.distance} m radius is large for a 3D model: expect heavy downloads, slow builds and "
              f"hundreds of thousands of buildings. {DEFAULT_DISTANCE_3D}-2500 m works best.")

    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        sys.exit(1)

    if args.all_themes:
        themes_to_generate = available_themes
    else:
        if args.theme not in available_themes:
            print(f"Error: Theme '{args.theme}' not found.")
            print(f"Available themes: {', '.join(available_themes)}")
            sys.exit(1)
        themes_to_generate = [args.theme]

    print("=" * 50)
    print("City Map Poster Generator")
    print("=" * 50)

    # Load custom fonts if specified
    custom_fonts = None
    if args.font_family:
        custom_fonts = load_fonts(args.font_family)
        if not custom_fonts:
            print(f"⚠ Failed to load '{args.font_family}', falling back to Roboto")

    # Get coordinates and generate poster
    try:
        if args.latitude and args.longitude:
            lat = parse(args.latitude)
            lon = parse(args.longitude)
            coords = [lat, lon]
            print(f"✓ Coordinates: {', '.join([str(i) for i in coords])}")
        else:
            coords = get_coordinates(args.city, args.country)

        if args.three_d:
            # The 3D scene does not depend on the theme: build it once, then render it per theme
            scene_3d = build_scene_3d(
                args.city,
                coords,
                args.distance,
                exaggeration=args.exaggeration,
                building_exaggeration=args.building_exaggeration,
                azimuth=args.azimuth,
                include_buildings=args.buildings,
                include_trees=args.trees,
                include_railways=args.railways,
                include_terrain=args.terrain,
                use_overture=args.overture,
                tiles=args.tiles,
                include_traffic=args.traffic,
                include_underground=args.underground,
            )

        for theme_name in themes_to_generate:
            set_active_theme(load_theme(theme_name))
            if args.three_d:
                output_file = generate_output_filename(args.city, f"{theme_name}_3d", args.format)
                create_poster_3d(
                    scene_3d,
                    args.city,
                    args.country,
                    output_file,
                    args.format,
                    args.width,
                    args.height,
                    outputs=outputs_3d,
                    azimuth=args.azimuth,
                    elevation=args.elevation,
                    zoom=args.zoom,
                    display_city=args.display_city or args.city,
                    display_country=args.display_country or args.country_label or args.country,
                    fonts=custom_fonts,
                    include_labels=args.labels,
                    print_size=args.print_size,
                    theme_name=theme_name,
                )
                continue
            output_file = generate_output_filename(args.city, theme_name, args.format)
            create_poster(
                args.city,
                args.country,
                coords,
                args.distance,
                output_file,
                args.format,
                args.width,
                args.height,
                country_label=args.country_label,
                display_city=args.display_city,
                display_country=args.display_country,
                fonts=custom_fonts,
                include_railways=args.railways,
                include_buildings=args.buildings,
                include_labels=args.labels,
                include_gradient=args.gradient,
            )

        print("\n" + "=" * 50)
        print("✓ 3D model generation complete!" if args.three_d else "✓ Poster generation complete!")
        print("=" * 50)

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
