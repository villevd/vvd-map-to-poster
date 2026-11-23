"""Aircraft specifications (the built-in library entries and their dimensions)."""

import dataclasses
from dataclasses import dataclass


FORMAT = "maptoposter-aircraft-library"
FORMAT_VERSION = 1
AXES = "glTF: nose +X, up +Y, left wing -Z; metres; origin on the ground under the fuselage middle"

BODY = (0.95, 0.95, 0.94)
WING = (0.80, 0.82, 0.85)
ENGINE = (0.55, 0.58, 0.62)
GEAR = (0.20, 0.20, 0.22)
PROP = (0.12, 0.12, 0.13)
ROTOR = (0.14, 0.14, 0.15)


class LibraryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Spec:
    id: str
    codes: tuple[str, ...]          # ICAO type designators this model stands for
    name: str
    maker: str
    category: str                   # airliner | regional | turboprop | light | helicopter
    length: float                   # overall length (helicopters: body + boom)
    span: float                     # wingspan (helicopters: main rotor diameter)
    height: float                   # overall height on the ground
    width: float                    # fuselage width
    gear: float                     # height of the fuselage belly above the ground (helicopters: skid body gap)
    wing: str                       # "low" or "high"
    engines: tuple[float, ...]      # spanwise engine positions per side, fraction of the half span
    mount: str                      # underwing | wing_front | rear | nose | heli
    engine_d: float                 # nacelle / engine diameter
    engine_len: float               # nacelle length
    tail: str                       # "conventional" or "t"
    blades: int = 0                 # propeller blades (0 for jets)
    prop_r: float = 0.0             # propeller radius
    winglets: bool = False
    wheel: float = 0.5              # wheel radius
    fuselage_h: float | None = None  # fuselage height when not round
    sweep: float | None = None      # leading-edge sweep, degrees
    accent: str = "#1F3C88"         # livery accent on fins and stabilizers
    wing_x: float = 0.36            # wing root leading edge, fraction of the length aft of the nose
    approx: bool = True
    generic: bool = False           # a stand-in for types the library does not list


def _spec(**kw) -> Spec:
    return Spec(**kw)


SPECS: tuple[Spec, ...] = (
    # --- narrowbody jets -------------------------------------------------------------------------
    _spec(id="a319", codes=("A319",), name="Airbus A319", maker="Airbus", category="airliner",
          length=33.8, span=35.8, height=11.8, width=3.95, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.75, engine_len=3.4, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="a320", codes=("A320",), name="Airbus A320", maker="Airbus", category="airliner",
          length=37.6, span=35.8, height=11.8, width=3.95, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.75, engine_len=3.5, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="a321", codes=("A321",), name="Airbus A321", maker="Airbus", category="airliner",
          length=44.5, span=35.8, height=11.8, width=3.95, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.75, engine_len=3.5, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="a20n", codes=("A20N",), name="Airbus A320neo", maker="Airbus", category="airliner",
          length=37.6, span=35.8, height=11.8, width=3.95, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.0, engine_len=3.9, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="a21n", codes=("A21N",), name="Airbus A321neo", maker="Airbus", category="airliner",
          length=44.5, span=35.8, height=11.8, width=3.95, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.0, engine_len=3.9, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="a220", codes=("BCS3", "BCS1"), name="Airbus A220-300", maker="Airbus", category="airliner",
          length=38.7, span=35.1, height=11.5, width=3.7, gear=2.0, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.8, engine_len=3.3, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="b738", codes=("B737", "B738"), name="Boeing 737-800", maker="Boeing", category="airliner",
          length=39.5, span=35.8, height=12.5, width=3.76, gear=2.0, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.55, engine_len=2.9, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="b38m", codes=("B38M",), name="Boeing 737 MAX 8", maker="Boeing", category="airliner",
          length=39.5, span=35.9, height=12.3, width=3.76, gear=2.0, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.75, engine_len=3.1, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="b39m", codes=("B39M",), name="Boeing 737 MAX 9", maker="Boeing", category="airliner",
          length=42.2, span=35.9, height=12.3, width=3.76, gear=2.0, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.75, engine_len=3.1, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="b739", codes=("B739",), name="Boeing 737-900", maker="Boeing", category="airliner",
          length=42.1, span=34.3, height=12.5, width=3.76, gear=2.0, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.55, engine_len=2.9, tail="conventional", winglets=True, wheel=0.6),
    _spec(id="b752", codes=("B752",), name="Boeing 757-200", maker="Boeing", category="airliner",
          length=47.3, span=38.1, height=13.6, width=3.8, gear=1.9, wing="low", engines=(0.33,),
          mount="underwing", engine_d=1.9, engine_len=3.6, tail="conventional", wheel=0.6),
    # --- widebody jets ----------------------------------------------------------------------------------
    _spec(id="b763", codes=("B763",), name="Boeing 767-300", maker="Boeing", category="airliner",
          length=54.9, span=47.6, height=15.8, width=5.03, gear=2.2, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.7, engine_len=4.6, tail="conventional", wheel=0.7, sweep=31.5),
    _spec(id="b788", codes=("B788",), name="Boeing 787-8", maker="Boeing", category="airliner",
          length=56.7, span=60.1, height=17.0, width=5.77, gear=2.5, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.8, engine_len=4.7, tail="conventional", wheel=0.7, sweep=32),
    _spec(id="b789", codes=("B789",), name="Boeing 787-9", maker="Boeing", category="airliner",
          length=62.8, span=60.1, height=17.0, width=5.77, gear=2.5, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.8, engine_len=4.7, tail="conventional", wheel=0.7, sweep=32),
    _spec(id="b77w", codes=("B77W",), name="Boeing 777-300ER", maker="Boeing", category="airliner",
          length=73.9, span=64.8, height=18.5, width=6.2, gear=2.5, wing="low", engines=(0.33,),
          mount="underwing", engine_d=3.3, engine_len=5.5, tail="conventional", winglets=True, wheel=0.7,
          sweep=31.6),
    _spec(id="b744", codes=("B744",), name="Boeing 747-400", maker="Boeing", category="airliner",
          length=70.7, span=64.4, height=19.4, width=6.5, gear=2.8, wing="low", engines=(0.36, 0.7),
          mount="underwing", engine_d=2.5, engine_len=4.6, tail="conventional", winglets=True, wheel=0.7,
          sweep=37.5),
    _spec(id="a333", codes=("A333",), name="Airbus A330-300", maker="Airbus", category="airliner",
          length=63.7, span=60.3, height=16.8, width=5.64, gear=2.5, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.85, engine_len=4.8, tail="conventional", winglets=True, wheel=0.7,
          sweep=30),
    _spec(id="a359", codes=("A359",), name="Airbus A350-900", maker="Airbus", category="airliner",
          length=66.8, span=64.75, height=17.1, width=5.96, gear=2.5, wing="low", engines=(0.33,),
          mount="underwing", engine_d=2.96, engine_len=5.2, tail="conventional", winglets=True, wheel=0.7,
          sweep=33),
    _spec(id="a388", codes=("A388",), name="Airbus A380-800", maker="Airbus", category="airliner",
          length=72.7, span=79.8, height=24.1, width=7.14, fuselage_h=8.41, gear=2.8, wing="low",
          engines=(0.28, 0.6), mount="underwing", engine_d=2.95, engine_len=5.0, tail="conventional",
          wheel=0.7, sweep=33.5),
    # --- regional jets -----------------------------------------------------------------------------------
    _spec(id="e170", codes=("E170",), name="Embraer E170", maker="Embraer", category="regional",
          length=29.9, span=26.0, height=8.5, width=2.8, gear=1.2, wing="low", engines=(0.3,),
          mount="underwing", engine_d=1.2, engine_len=2.5, tail="t", wheel=0.45, sweep=25),
    _spec(id="e75l", codes=("E75L", "E175"), name="Embraer E175", maker="Embraer", category="regional",
          length=31.7, span=28.7, height=9.7, width=2.8, gear=1.2, wing="low", engines=(0.3,),
          mount="underwing", engine_d=1.25, engine_len=2.6, tail="t", wheel=0.45, sweep=25),
    _spec(id="e190", codes=("E190",), name="Embraer E190", maker="Embraer", category="regional",
          length=36.2, span=28.7, height=10.7, width=3.0, gear=1.3, wing="low", engines=(0.3,),
          mount="underwing", engine_d=1.5, engine_len=2.9, tail="t", wheel=0.5, sweep=25),
    _spec(id="e195", codes=("E195",), name="Embraer E195", maker="Embraer", category="regional",
          length=38.7, span=28.7, height=10.7, width=3.0, gear=1.3, wing="low", engines=(0.3,),
          mount="underwing", engine_d=1.5, engine_len=2.9, tail="t", wheel=0.5, sweep=25),
    _spec(id="crj2", codes=("CRJ2",), name="Bombardier CRJ200", maker="Bombardier", category="regional",
          length=26.8, span=21.2, height=6.2, width=2.7, gear=1.1, wing="low", engines=(0.0,),
          mount="rear", engine_d=1.0, engine_len=2.4, tail="t", wheel=0.45, sweep=25),
    _spec(id="crj7", codes=("CRJ7",), name="Bombardier CRJ700", maker="Bombardier", category="regional",
          length=32.5, span=23.2, height=7.5, width=2.7, gear=1.1, wing="low", engines=(0.0,),
          mount="rear", engine_d=1.2, engine_len=2.4, tail="t", wheel=0.45, sweep=25),
    _spec(id="crj9", codes=("CRJ9",), name="Bombardier CRJ900", maker="Bombardier", category="regional",
          length=36.2, span=24.9, height=7.5, width=2.7, gear=1.1, wing="low", engines=(0.0,),
          mount="rear", engine_d=1.25, engine_len=2.5, tail="t", wheel=0.45, sweep=25),
    # --- turboprops ---------------------------------------------------------------------------------------
    _spec(id="dh8d", codes=("DH8D",), name="De Havilland Dash 8-400", maker="De Havilland Canada",
          category="turboprop", length=32.8, span=28.4, height=8.4, width=2.8, gear=1.5, wing="high",
          engines=(0.3,), mount="wing_front", engine_d=1.2, engine_len=2.6, tail="conventional",
          blades=6, prop_r=1.95, wheel=0.5, sweep=3, accent="#0B6E4F"),
    _spec(id="dh8c", codes=("DH8C",), name="De Havilland Dash 8-300", maker="De Havilland Canada",
          category="turboprop", length=25.7, span=27.4, height=7.5, width=2.7, gear=1.4, wing="high",
          engines=(0.3,), mount="wing_front", engine_d=1.0, engine_len=2.2, tail="conventional",
          blades=4, prop_r=1.7, wheel=0.5, sweep=3, accent="#0B6E4F"),
    _spec(id="at76", codes=("AT76", "AT72", "AT75"), name="ATR 72", maker="ATR", category="turboprop",
          length=27.2, span=27.05, height=7.65, width=2.9, gear=1.3, wing="high", engines=(0.42,),
          mount="wing_front", engine_d=1.0, engine_len=2.6, tail="conventional", blades=6, prop_r=1.95,
          wheel=0.5, sweep=0, wing_x=0.42, accent="#0B6E4F"),
    # --- light aircraft --------------------------------------------------------------------------------------
    _spec(id="c208", codes=("C208",), name="Cessna 208 Caravan", maker="Textron Aviation", category="light",
          length=11.5, span=15.9, height=3.8, width=1.6, fuselage_h=1.9, gear=0.8, wing="high", engines=(0.0,),
          mount="nose", engine_d=0.8, engine_len=1.8, tail="conventional", blades=3, prop_r=1.5,
          wheel=0.25, sweep=0, wing_x=0.42, accent="#1F3C88"),
    _spec(id="c172", codes=("C172",), name="Cessna 172", maker="Textron Aviation", category="light",
          length=8.3, span=11.0, height=2.7, width=1.25, gear=0.45, wing="high", engines=(0.0,),
          mount="nose", engine_d=0.45, engine_len=0.9, tail="conventional", blades=2, prop_r=0.97,
          wheel=0.2, sweep=0, accent="#C62828"),
    _spec(id="pc12", codes=("PC12",), name="Pilatus PC-12", maker="Pilatus", category="light",
          length=14.4, span=16.3, height=4.3, width=1.6, fuselage_h=2.2, gear=0.6, wing="high", engines=(0.0,),
          mount="nose", engine_d=0.6, engine_len=1.9, tail="t", blades=4, prop_r=1.45, wheel=0.25,
          sweep=0, accent="#37474F"),
    # --- helicopters ----------------------------------------------------------------------------------------------
    _spec(id="b06", codes=("B06", "B206"), name="Bell 206 JetRanger", maker="Bell", category="helicopter",
          length=9.5, span=10.2, height=2.9, width=1.5, gear=0.6, wing="high", engines=(0.0,), mount="heli",
          engine_d=0.0, engine_len=0.0, tail="t", wheel=0.0, fuselage_h=1.3, accent="#C62828"),
    _spec(id="ec35", codes=("EC35", "EC30"), name="Airbus H135", maker="Airbus Helicopters",
          category="helicopter", length=10.2, span=10.2, height=3.4, width=1.6, gear=0.6, wing="high",
          engines=(0.0,), mount="heli", engine_d=0.0, engine_len=0.0, tail="t", wheel=0.0, fuselage_h=1.6,
          accent="#1F3C88"),
    _spec(id="r44", codes=("R44",), name="Robinson R44", maker="Robinson", category="helicopter",
          length=9.0, span=10.1, height=2.9, width=1.2, gear=0.5, wing="high", engines=(0.0,), mount="heli",
          engine_d=0.0, engine_len=0.0, tail="t", wheel=0.0, fuselage_h=1.3, accent="#F9A825"),
)

# Fallbacks for types the library does not list: same geometry, no ICAO codes of their own.
GENERIC_SPECS: tuple[Spec, ...] = (
    dataclasses.replace(next(s for s in SPECS if s.id == "a320"), id="generic-jet", codes=(),
                        name="Generic airliner", maker="", generic=True),
    dataclasses.replace(next(s for s in SPECS if s.id == "at76"), id="generic-turboprop", codes=(),
                        name="Generic turboprop", maker="", generic=True),
    dataclasses.replace(next(s for s in SPECS if s.id == "c172"), id="generic-light", codes=(),
                        name="Generic light aircraft", maker="", generic=True),
    dataclasses.replace(next(s for s in SPECS if s.id == "b06"), id="generic-heli", codes=(),
                        name="Generic helicopter", maker="", generic=True),
)

ALL_SPECS: tuple[Spec, ...] = SPECS + GENERIC_SPECS
