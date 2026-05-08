#!/usr/bin/env python3
"""
poi_ontology_verify.py  (v2 — dynamic discovery)
=================================================
Comprehensive audit of ards_poi_ontology_v5.owl (or v6 after cleaning).

Changes from v1
---------------
- Discovers actual property IRIs from the OWL file dynamically rather than
  hardcoding expected snake_case / camelCase names
- Label search uses multi-candidate matching (exact, lowercase, partial)
  to handle existing v4 classes with short labels ("ARDS", "LTV", etc.)
- Check 9 (subphenotype hierarchy) now uses IRIs found in Check 1
- Reports actual property names found, not just "MISSING"

Checks
------
1.  All 36 new classes present (by label, with fuzzy fallback)
2.  OWL Object Properties declared — count + list with xref status
3.  OWL Data Properties declared — count + list
4.  Key definitions (IAO_0000115) populated
5.  SNOMED / CHEBI equivalentClass mappings
6.  disjointWith axioms
7.  BioLink structural contamination (must be ZERO in OWL layer)
8.  Legacy KG predicate contamination (poi:BENEFICIAL / HARMFUL / NULL etc.)
9.  rdfs:subClassOf chain for ARDS subphenotypes
10. BioLink xref annotations on Object Properties

Usage
-----
    python3 poi_ontology_verify.py \\
        corpus_analysis/ontology_output/ards_poi_ontology_v5.owl

    # Or v6 after cleaning:
    python3 poi_ontology_verify.py \\
        corpus_analysis/ontology_output/ards_poi_ontology_v6.owl
"""

import sys
from pathlib import Path
from collections import defaultdict

try:
    from rdflib import Graph, Namespace, RDF, RDFS, OWL, URIRef, Literal
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install",
                    "rdflib", "--break-system-packages", "-q"], check=True)
    from rdflib import Graph, Namespace, RDF, RDFS, OWL, URIRef, Literal

POI     = Namespace("http://poi-kb.org/ards/")
OBO     = Namespace("http://purl.obolibrary.org/obo/")
BIOLINK = Namespace("https://w3id.org/biolink/vocab/")
SNOMED  = Namespace("http://snomed.info/id/")
CHEBI   = Namespace("http://purl.obolibrary.org/obo/CHEBI_")

IN_SUBSET   = OBO["inSubset"]
HAS_DB_XREF = OBO["hasDbXref"]
HAS_EXACT_SYN = OBO["hasExactSynonym"]
IAO_DEF     = URIRef("http://purl.obolibrary.org/obo/IAO_0000115")

# ── Expected new classes (as added by enhancer v2) ────────────────────────────
EXPECTED_NEW_CLASSES = [
    "Hyperinflammatory ARDS subphenotype",
    "Hypoinflammatory ARDS subphenotype",
    "Focal ARDS",
    "Non-focal ARDS",
    "COVID-19-associated ARDS",
    "Interleukin-6",
    "Interleukin-8",
    "Soluble receptor for advanced glycation end-products",
    "Angiopoietin-2",
    "Plasminogen activator inhibitor-1",
    "Surfactant protein D",
    "Club cell secretory protein 16",
    "Tumor necrosis factor alpha",
    "Baricitinib",
    "High-flow nasal cannula oxygen therapy",
    "Awake prone positioning",
    "Ultra-protective ventilation during VV-ECMO",
    "Rescue VV-ECMO",
    "Prone positioning during VV-ECMO",
    "Electrical impedance tomography",
    "Transpulmonary pressure monitoring",
    "Patient self-inflicted lung injury",
    "Volutrauma",
    "Atelectrauma",
    "Biotrauma",
    "Driving pressure",
    "Mechanical power",
    "Mesenteric ischemia in ARDS",
    "Right ventricular failure in ARDS",
    "Morbid obesity in ARDS population",
    "Right ventricular dysfunction ARDS population",
    "Immunocompromised patient with ARDS",
    "First 48 hours ARDS context",
    "Severe ARDS context PaO2/FiO2 <150",
    "One-lung ventilation surgical context",
    "Lighter sedation era clinical context",
]

# Known alternative labels for existing v4 classes (short vs long form)
LABEL_ALIASES = {
    "acute respiratory distress syndrome": [
        "acute respiratory distress syndrome","ards","ards (acute respiratory distress syndrome)",
        "acute lung injury","ali/ards",
    ],
    "low tidal volume strategy": [
        "low tidal volume strategy","low tidal volume ventilation","lung-protective ventilation",
        "low-tidal-volume ventilation","lpv","tidal volume 6 ml/kg pbw",
    ],
    "vv-ecmo": [
        "vv-ecmo","veno-venous ecmo","venovenous ecmo","vv ecmo",
        "extracorporeal membrane oxygenation","ecmo",
    ],
    "dexamethasone": [
        "dexamethasone","dexamethasone therapy","dexamethasone treatment","dxm","dex",
    ],
    "cisatracurium": [
        "cisatracurium","cisatracurium besylate","neuromuscular blockade","nmb",
        "neuromuscular blocking agent",
    ],
    "prone positioning": [
        "prone positioning","prone position","proning","prone position therapy",
        "prone ventilation",
    ],
}

EXPECTED_EQUIV_CLASSES = [
    ("Acute respiratory distress syndrome / ARDS", "SNOMED:67782005",
     "http://snomed.info/id/67782005"),
    ("Prone positioning",                          "SNOMED:431182000",
     "http://snomed.info/id/431182000"),
    ("HFNC oxygen therapy",                        "SNOMED:371907003",
     "http://snomed.info/id/371907003"),
    ("Dexamethasone",                              "SNOMED:372584003",
     "http://snomed.info/id/372584003"),
    ("Dexamethasone",                              "CHEBI:41879",
     "http://purl.obolibrary.org/obo/CHEBI_41879"),
    ("Interleukin-6",                              "CHEBI:138181",
     "http://purl.obolibrary.org/obo/CHEBI_138181"),
    ("Baricitinib",                                "CHEBI:90551",
     "http://purl.obolibrary.org/obo/CHEBI_90551"),
]

# Legacy predicates that must NOT appear as used predicates
LEGACY_PREDICATES = [
    "BENEFICIAL","HARMFUL","NULL","TREND","MECHANISM","MIXED",
    "ASSOCIATION","confidence","weight","source_trial",
]

# BioLink IRIs that must NOT appear as structural OWL elements
BL_STRUCTURAL_LOCALS = [
    "treats","causes","contraindicated_in","has_biomarker","biomarker_for",
    "prevents","precedes","treated_by","ameliorates_condition","disrupts",
    "occurs_in","positively_regulates","negatively_regulates","contributes_to",
    "has_part","has_phenotype","interacts_with","affects","related_to",
]


# ── Label matching ─────────────────────────────────────────────────────────────

def build_label_index(g):
    """Build label → IRI index. Stores ALL IRIs per lowercase label."""
    idx = defaultdict(list)
    for s, _, o in g.triples((None, RDFS.label, None)):
        idx[str(o).lower().strip()].append(s)
    # Also index by synonym
    for s, _, o in g.triples((None, HAS_EXACT_SYN, None)):
        idx[str(o).lower().strip()].append(s)
    return idx

def find_cls(idx, label, aliases=None):
    """Find a class IRI by label, trying aliases if exact match fails."""
    key = label.lower().strip()
    hits = idx.get(key, [])
    if hits:
        return hits[0]
    # Try aliases
    for alias in (aliases or []):
        hits = idx.get(alias.lower().strip(), [])
        if hits:
            return hits[0]
    # Try partial match (label is a substring of a stored key)
    for stored_key, iris in idx.items():
        if key in stored_key or stored_key in key:
            return iris[0]
    return None

def find_cls_multi(idx, label, alias_dict=None):
    """Try all aliases from LABEL_ALIASES dict."""
    key = label.lower().strip()
    aliases = (alias_dict or LABEL_ALIASES).get(key, [])
    return find_cls(idx, label, aliases)


# ── Formatting ────────────────────────────────────────────────────────────────

def col(text, code):  return f"\033[{code}m{text}\033[0m"
def ok(msg):   print(col(f"  ✓  {msg}", "32"))
def fail(msg): print(col(f"  ✗  {msg}", "31"))
def warn(msg): print(col(f"  ⚠  {msg}", "33"))
def hdr(msg):  print(col(f"\n── {msg} ──", "36"))


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    SCRIPT_DIR = Path(__file__).resolve().parent
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if not path.is_absolute():
            path = Path.cwd() / path
    else:
        path = SCRIPT_DIR / "corpus_analysis" / "ontology_output" / "ards_poi_ontology_v5.owl"

    print(f"\n{'='*64}")
    print("POI-KB Ontology — Verification Audit  (v2)")
    print(f"{'='*64}")
    print(f"File: {path}")

    if not path.exists():
        print(f"\nERROR: File not found: {path}")
        sys.exit(1)

    g = Graph()
    g.parse(str(path))
    total_triples = len(g)
    print(f"Loaded: {total_triples:,} triples\n")

    idx = build_label_index(g)
    results = []
    found_new_iris = {}   # label → IRI, populated in Check 1 for later use

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 1: New classes
    # ─────────────────────────────────────────────────────────────────────────
    hdr("CHECK 1: New Domain Classes (expected 36)")
    missing = []
    for label in EXPECTED_NEW_CLASSES:
        iri = find_cls(idx, label)
        if iri:
            ok(label)
            found_new_iris[label] = iri
        else:
            fail(f"MISSING: {label}")
            missing.append(label)
    passed = len(EXPECTED_NEW_CLASSES) - len(missing)
    results.append(("New classes", passed, len(EXPECTED_NEW_CLASSES)))

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 2: OWL Object Properties — DYNAMIC DISCOVERY
    # ─────────────────────────────────────────────────────────────────────────
    hdr("CHECK 2: OWL Object Properties (dynamic discovery)")
    all_obj_props = list(g.subjects(RDF.type, OWL.ObjectProperty))
    poi_obj_props = [p for p in all_obj_props
                     if str(p).startswith("http://poi-kb.org/ards/")]
    other_obj_props = [p for p in all_obj_props
                       if not str(p).startswith("http://poi-kb.org/ards/")]

    print(col(f"  Found {len(poi_obj_props)} poi: Object Properties:", "36"))
    print()
    for p in sorted(poi_obj_props, key=lambda x: str(x)):
        local = str(p).split("/")[-1]
        labels = [str(o) for o in g.objects(p, RDFS.label)]
        xrefs  = [str(o) for o in g.objects(p, HAS_DB_XREF)]
        defs   = list(g.objects(p, IAO_DEF))
        bl_xref = next((x for x in xrefs if x.startswith("biolink:")), None)
        ro_xref = next((x for x in xrefs if x.startswith("RO:") or x.startswith("BFO:")), None)
        has_lbl = bool(labels)
        has_def = bool(defs)
        detail = (
            f"label={'✓' if has_lbl else '✗'}  "
            f"def={'✓' if has_def else '✗'}  "
            f"RO={'✓ '+ro_xref if ro_xref else '✗'}  "
            f"BioLink={'✓ '+bl_xref if bl_xref else '✗'}"
        )
        ok(f"poi:{local}  [{detail}]")

    if other_obj_props:
        print()
        warn(f"  {len(other_obj_props)} non-POI object property IRI(s) also declared:")
        for p in other_obj_props:
            warn(f"    {p}")

    # Expected minimum count
    MIN_EXPECTED = 20
    passed_n = len(poi_obj_props)
    results.append(("Object Properties (≥20)", min(passed_n, MIN_EXPECTED), MIN_EXPECTED))

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 3: OWL Data Properties — DYNAMIC DISCOVERY
    # ─────────────────────────────────────────────────────────────────────────
    hdr("CHECK 3: OWL Data Properties (dynamic discovery)")
    all_data_props = list(g.subjects(RDF.type, OWL.DatatypeProperty))
    poi_data_props = [p for p in all_data_props
                      if str(p).startswith("http://poi-kb.org/ards/")]

    print(col(f"  Found {len(poi_data_props)} poi: Data Properties:", "36"))
    for p in sorted(poi_data_props, key=lambda x: str(x)):
        local  = str(p).split("/")[-1]
        ranges = [str(o).split("#")[-1].split("/")[-1]
                  for o in g.objects(p, RDFS.range)]
        rng_str = ", ".join(ranges) if ranges else "?"
        ok(f"poi:{local}  [{rng_str}]")

    MIN_DATA = 15
    results.append(("Data Properties (≥15)", min(len(poi_data_props), MIN_DATA), MIN_DATA))

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 4: Definitions on key classes
    # ─────────────────────────────────────────────────────────────────────────
    hdr("CHECK 4: IAO_0000115 Definitions (key classes)")
    # ARDS root class is now ARDS_POI_03270 (created by finalizer)
    # as v4 had no canonical ARDS class
    KEY_CLASSES = [
        ("Acute respiratory distress syndrome / ARDS",
         ["acute respiratory distress syndrome","ards",
          "ards_poi_03270"]),
        ("Prone positioning",
         ["prone positioning","prone position","proning"]),
        ("Low tidal volume strategy / LPV",
         ["low tidal volume strategy","lung-protective ventilation",
          "low tidal volume ventilation"]),
        ("VV-ECMO",
         ["vv-ecmo","veno-venous ecmo","ecmo"]),
        ("Dexamethasone",
         ["dexamethasone","dex"]),
        ("Cisatracurium / NMB",
         ["cisatracurium","neuromuscular blockade","nmb"]),
        ("Hyperinflammatory ARDS subphenotype",
         ["hyperinflammatory ards subphenotype"]),
        ("Volutrauma",
         ["volutrauma"]),
        ("Atelectrauma",
         ["atelectrauma"]),
        ("Biotrauma",
         ["biotrauma"]),
        ("Patient self-inflicted lung injury",
         ["patient self-inflicted lung injury","p-sili"]),
    ]
    def_found = 0
    for display, search_labels in KEY_CLASSES:
        iri = None
        for lbl in search_labels:
            iri = find_cls(idx, lbl)
            if iri:
                break
        if not iri:
            warn(f"Class not found in index: {display}")
            warn(f"  (searched: {search_labels[:2]}...)")
        else:
            has_def = bool(list(g.objects(iri, IAO_DEF)))
            if has_def:
                defn = str(list(g.objects(iri, IAO_DEF))[0])[:80]
                ok(f"{display}  → \"{defn}...\"")
                def_found += 1
            else:
                fail(f"No definition: {display}  (IRI: {str(iri).split('/')[-1]})")
    results.append(("Definitions", def_found, len(KEY_CLASSES)))

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 5: equivalentClass mappings
    # ─────────────────────────────────────────────────────────────────────────
    hdr("CHECK 5: owl:equivalentClass (SNOMED-CT, CHEBI)")
    all_equiv_objects = {str(o) for _, o in g.subject_objects(OWL.equivalentClass)}
    eq_pass = 0
    for display, curie, uri in EXPECTED_EQUIV_CLASSES:
        if uri in all_equiv_objects:
            ok(f"{display}  ↔  {curie}")
            eq_pass += 1
        else:
            fail(f"MISSING: {display}  ↔  {curie}")
    results.append(("equivalentClass", eq_pass, len(EXPECTED_EQUIV_CLASSES)))

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 6: disjointWith
    # ─────────────────────────────────────────────────────────────────────────
    hdr("CHECK 6: owl:disjointWith axioms")
    dj_count = len(list(g.subject_objects(OWL.disjointWith)))
    # Expected ≥3 class-level disjointWith (hyperinflam⊥hypoinflam, focal⊥nonfocal,
    # volutrauma⊥atelectrauma). Subset-level disjointWith were removed in v9_fixed
    # as they caused ELK inconsistency — now stored as rdfs:comment instead.
    if dj_count >= 3:
        ok(f"{dj_count} disjointWith axioms present (expected ≥3 class-level)")
    else:
        fail(f"Only {dj_count} disjointWith axioms (expected ≥3 class-level)")

    # Spot-check specific pairs
    pairs = [
        ("hyperinflammatory ards subphenotype","hypoinflammatory ards subphenotype","Hyperinflam ⊥ Hypoinflam"),
        ("focal ards","non-focal ards","Focal ⊥ Non-focal ARDS"),
        ("volutrauma","atelectrauma","Volutrauma ⊥ Atelectrauma"),
    ]
    for l1, l2, display in pairs:
        i1 = find_cls(idx, l1)
        i2 = find_cls(idx, l2)
        if i1 and i2:
            if (i1, OWL.disjointWith, i2) in g or (i2, OWL.disjointWith, i1) in g:
                ok(display)
            else:
                fail(f"MISSING: {display}")
        else:
            warn(f"Cannot check {display} (class IRI not found)")
    results.append(("disjointWith (≥3)", min(dj_count,3), 3))

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 7: BioLink structural contamination (MUST BE ZERO)
    # ─────────────────────────────────────────────────────────────────────────
    hdr("CHECK 7: BioLink structural contamination (must be ZERO)")
    violations = []
    for s, _, o in g.triples((None, OWL.equivalentProperty, None)):
        if str(o).startswith("https://w3id.org/biolink/vocab/"):
            violations.append(f"owl:equivalentProperty → {o}")
    for s, _, o in g.triples((None, RDFS.subPropertyOf, None)):
        if str(o).startswith("https://w3id.org/biolink/vocab/"):
            violations.append(f"rdfs:subPropertyOf → {o}")
    for s, _, o in g.triples((None, RDFS.subClassOf, None)):
        if str(o).startswith("https://w3id.org/biolink/vocab/"):
            violations.append(f"rdfs:subClassOf → {o}")
    for bl_local in BL_STRUCTURAL_LOCALS:
        bl_iri = BIOLINK[bl_local]
        if (bl_iri, RDF.type, OWL.ObjectProperty) in g:
            violations.append(f"biolink:{bl_local} declared as owl:ObjectProperty")
        if (bl_iri, RDF.type, OWL.Class) in g:
            violations.append(f"biolink:{bl_local} declared as owl:Class")

    if violations:
        for v in violations:
            fail(f"STRUCTURAL VIOLATION: {v}")
        results.append(("BioLink clean", 0, 1))
    else:
        ok("CLEAN — zero BioLink structural triples in OWL layer")
        ok("BioLink only appears as oboInOwl:hasDbXref annotation strings")
        results.append(("BioLink clean", 1, 1))

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 8: Legacy KG predicate contamination
    # ─────────────────────────────────────────────────────────────────────────
    hdr("CHECK 8: Legacy KG predicate contamination")
    legacy_found = {}
    for local in LEGACY_PREDICATES:
        pred = POI[local]
        count = sum(1 for _ in g.triples((None, pred, None)))
        if count:
            legacy_found[local] = count

    if legacy_found:
        fail("LEGACY PREDICATES PRESENT (belong in KG layer, not OWL):")
        for pred, count in sorted(legacy_found.items(), key=lambda x: -x[1]):
            fail(f"  poi:{pred:<20} {count:>5} triple(s)")
        print()
        warn("  FIX: python3 poi_legacy_cleaner.py \\")
        warn(f"    {path} \\")
        warn(f"    {path.parent / path.name.replace('_v5','_v6').replace('_v4','_v6')}")
        results.append(("No legacy predicates", 0, 1))
    else:
        ok("CLEAN — no legacy poi:BENEFICIAL / poi:HARMFUL / poi:NULL predicates")
        results.append(("No legacy predicates", 1, 1))

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 9: Subphenotype rdfs:subClassOf hierarchy
    # Robust: collect ALL ARDS IRIs (multiple may exist from iterative editing)
    # ─────────────────────────────────────────────────────────────────────────
    hdr("CHECK 9: rdfs:subClassOf — ARDS subphenotype hierarchy")

    # Also search for the canonical root IRI label added by finalizer
    ARDS_SEARCH = {"ards","acute respiratory distress syndrome","ali/ards",
                   "ards (acute respiratory distress syndrome)",
                   "ards_poi_03270","acute respiratory distress syndrome"}
    ards_iris = set()
    for term in ARDS_SEARCH:
        from collections import defaultdict as _dd
        hits = idx.get(term, []) if isinstance(idx.get(term,[]), list) else [idx[term]]
        for h in hits:
            if (h, RDF.type, OWL.Class) in g:
                ards_iris.add(h)
    # Also handle single-value index (idx stores first match)
    for term in ARDS_SEARCH:
        h = idx.get(term)
        if h and not isinstance(h, list) and (h, RDF.type, OWL.Class) in g:
            ards_iris.add(h)

    if ards_iris:
        ok(f"Found {len(ards_iris)} ARDS IRI(s): "
           f"{[str(i).split('/')[-1] for i in sorted(ards_iris, key=str)]}")
    else:
        warn("No ARDS class IRI found in index")

    sp_passed = 0
    sp_labels = [
        "Hyperinflammatory ARDS subphenotype",
        "Hypoinflammatory ARDS subphenotype",
        "Focal ARDS",
        "Non-focal ARDS",
        "COVID-19-associated ARDS",
    ]
    for lbl in sp_labels:
        sp_iri = found_new_iris.get(lbl) or find_cls(idx, lbl)
        if not sp_iri:
            warn(f"  {lbl}: IRI not found")
            continue
        if not ards_iris:
            warn(f"  {lbl}: cannot check (no ARDS IRI found)")
            continue
        parents = set(g.objects(sp_iri, RDFS.subClassOf))
        linked = parents & ards_iris   # intersection: parents that are ARDS IRIs
        if linked:
            ok(f"{lbl}  rdfs:subClassOf  {[str(p).split('/')[-1] for p in linked]}")
            sp_passed += 1
        else:
            warn(f"{lbl}: no ARDS IRI in parents")
            warn(f"  Current parents: {[str(p).split('/')[-1] for p in parents]}")
    results.append(("Subphenotype hierarchy", sp_passed, len(sp_labels)))

    # ─────────────────────────────────────────────────────────────────────────
    # CHECK 10: BioLink xref on Object Properties
    # ─────────────────────────────────────────────────────────────────────────
    hdr("CHECK 10: BioLink xref annotations on poi: Object Properties")
    bl_xref_found = 0
    no_bl_xref = []
    for p in sorted(poi_obj_props, key=lambda x: str(x)):
        local = str(p).split("/")[-1]
        xrefs = [str(o) for o in g.objects(p, HAS_DB_XREF)]
        bl = next((x for x in xrefs if x.startswith("biolink:")), None)
        if bl:
            ok(f"poi:{local}  →  {bl}")
            bl_xref_found += 1
        else:
            warn(f"poi:{local}  — no biolink: xref (has {len(xrefs)} other xref(s))")
            no_bl_xref.append(local)

    if no_bl_xref:
        print()
        warn(f"  {len(no_bl_xref)} properties lack biolink: xref.")
        warn("  These need hasDbXref 'biolink:XXXX' added in the enhancer script.")
    results.append(("BioLink xrefs on props", bl_xref_found, len(poi_obj_props)))

    # ─────────────────────────────────────────────────────────────────────────
    # EXTRA: What predicate IRIs are actually in use (not declared as OWL props)?
    # ─────────────────────────────────────────────────────────────────────────
    hdr("EXTRA: All POI predicates used in triples (declared vs undeclared)")
    pred_usage = defaultdict(int)
    for _, p, _ in g:
        if str(p).startswith("http://poi-kb.org/ards/"):
            pred_usage[p] += 1

    declared = set(poi_obj_props) | set(poi_data_props)
    print(col("  Declared as OWL property and used:", "32"))
    for p in sorted(declared, key=lambda x: str(x)):
        local = str(p).split("/")[-1]
        used  = pred_usage.get(p, 0)
        print(f"    poi:{local}  ({used} triples as predicate)")

    undeclared_used = {p: c for p, c in pred_usage.items() if p not in declared}
    if undeclared_used:
        print()
        print(col(f"  Undeclared (raw RDF predicates — {len(undeclared_used)} found):", "31"))
        for p, c in sorted(undeclared_used.items(), key=lambda x: -x[1]):
            local = str(p).split("/")[-1]
            print(col(f"    poi:{local}  {c} triple(s)  ← NOT an OWL property", "31"))

    # ─────────────────────────────────────────────────────────────────────────
    # SUMMARY TABLE
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{'='*64}")
    print("VERIFICATION SUMMARY")
    print(f"{'='*64}")
    print(f"{'Check':<38}  {'Passed':>7}  {'Status':>8}")
    print("-" * 64)

    all_pass = True
    for section, passed, total in results:
        status = "✓ PASS" if passed == total else ("⚠ WARN" if passed > 0 else "✗ FAIL")
        c = "32" if passed == total else ("33" if passed > 0 else "31")
        if passed < total:
            all_pass = False
        print(col(f"  {section:<36}  {passed:>3}/{total:<3}  {status:>8}", c))

    print(f"\n{'='*64}")
    print(f"  Triples            : {total_triples:,}")
    print(f"  OWL Classes        : {len(list(g.subjects(RDF.type, OWL.Class))):,}")
    print(f"  poi: Obj Props     : {len(poi_obj_props)}")
    print(f"  poi: Data Props    : {len(poi_data_props)}")
    print(f"  Definitions (IAO)  : {len(list(g.subject_objects(IAO_DEF)))}")
    print(f"  Synonyms           : {len(list(g.subject_objects(HAS_EXACT_SYN)))}")
    print(f"  Cross-references   : {len(list(g.subject_objects(HAS_DB_XREF)))}")
    print(f"{'='*64}")

    if all_pass:
        print(col("\n  ALL CHECKS PASSED\n", "32"))
    else:
        print(col("\n  ISSUES FOUND — see details above\n", "33"))
        if legacy_found:
            total_legacy = sum(legacy_found.values())
            print(col(f"  PRIORITY: {total_legacy:,} legacy KG predicate triples", "31"))
            print(col("  Run poi_legacy_cleaner.py to produce v6", "31"))


if __name__ == "__main__":
    main()
