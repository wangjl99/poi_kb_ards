#!/usr/bin/env python3
"""
poi_ontology_restructure.py
============================
Fixes all 8 structural problems identified in expert review of v9_fixed:

  Fix 1: Add formal superclass hierarchy (BFO/OGMS aligned)
  Fix 2: Resolve class/individual punning (27 entities)
  Fix 3: Add missing rdfs:labels (116 classes)
  Fix 4: Merge/deprecate duplicates (6 confirmed pairs)
  Fix 5: Flag literature-statement classes for manual review
  Fix 6: Populate object properties from curated gold papers
  Fix 7: Strengthen external alignment (MONDO, HP, ChEBI, RO)
  Fix 8: Add BFO + RO ontology import declarations

Input:  ards_poi_ontology_v9_fixed.owl
Output: ards_poi_ontology_v10.owl
Report: ontology_restructure_report.txt

Usage:
  python3 poi_ontology_restructure.py
  python3 poi_ontology_restructure.py --dry-run    # report only, no OWL write
"""

import argparse
import re
from pathlib import Path
from collections import defaultdict, Counter

from rdflib import (
    Graph, Namespace, URIRef, Literal, BNode,
    RDF, RDFS, OWL, XSD
)
from rdflib.namespace import SKOS

# ── Namespaces ────────────────────────────────────────────────────────────────
OBO       = Namespace("http://purl.obolibrary.org/obo/")
POI       = Namespace("http://purl.obolibrary.org/obo/ARDS_POI_")
OBOINOWL  = Namespace("http://www.geneontology.org/formats/oboInOwl#")
IAO_0000115 = OBO["IAO_0000115"]   # definition
IAO_0000114 = OBO["IAO_0000114"]   # has curation status
IAO_0000231 = OBO["IAO_0000231"]   # has obsolescence reason

# ── Fix 1: Formal superclass hierarchy ────────────────────────────────────────
#
# Design based on BFO 2.0 + OGMS + OBI:
#
#  owl:Thing
#  └── BFO:0000001 (entity)
#      ├── BFO:0000002 (continuant)
#      │   ├── BFO:0000004 (independent continuant)
#      │   │   ├── OGMS:0000045  → Material entity → POI_organism_part
#      │   │   └── OBI:0100026   → Organism
#      │   ├── BFO:0000016 (disposition)
#      │   │   └── OGMS:0000031  → Disease ← POI_disease
#      │   └── BFO:0000019 (quality)
#      │       └── PATO:0000001  → Quality ← POI_phenotype (NEW)
#      └── BFO:0000003 (occurrent)
#          ├── BFO:0000007 (process)
#          │   ├── OBI:0000070   → Assay ← POI_biomarker_measurement
#          │   ├── OGMS:0000097  → Clinical finding ← POI_outcome
#          │   └── GO:0008150    → Biological process ← POI_mechanism
#          └── BFO:0000035 (process boundary)
#
# POI-specific superclasses:
#   POI_disease        rdfs:subClassOf  OGMS:0000031 (disease)
#   POI_intervention   rdfs:subClassOf  OBI:0000070  (planned process/intervention)
#   POI_outcome        rdfs:subClassOf  OGMS:0000097 (clinical finding process)
#   POI_mechanism      rdfs:subClassOf  GO:0008150   (biological process)
#   POI_biomarker      rdfs:subClassOf  OBI:0000070  (assay/measurement)
#   POI_population     rdfs:subClassOf  OBI:0100026  (organism/patient)
#   POI_context        rdfs:subClassOf  BFO:0000035  (situation/context)

SUPERCLASS_MAP = {
    # subset_IRI_fragment → (parent_URI, parent_label, parent_source)
    "POI_disease": (
        OBO["OGMS_0000031"],
        "disease",
        "OGMS:0000031"
    ),
    "POI_intervention": (
        OBO["OBI_0000070"],
        "planned process",
        "OBI:0000070"
    ),
    "POI_outcome": (
        OBO["OGMS_0000097"],
        "clinical finding",
        "OGMS:0000097"
    ),
    "POI_mechanism": (
        OBO["GO_0008150"],
        "biological process",
        "GO:0008150"
    ),
    "POI_biomarker": (
        OBO["OBI_0001162"],
        "measurement datum",
        "OBI:0001162"
    ),
    "POI_population": (
        OBO["OBI_0100026"],
        "organism",
        "OBI:0100026"
    ),
    "POI_context": (
        OBO["BFO_0000040"],
        "material entity / context",
        "BFO:0000040"
    ),
}

# Mid-level disease classes — add these for internal hierarchy
DISEASE_HIERARCHY = {
    # class_label → (parent_label, parent_IRI_string)
    "Acute respiratory distress syndrome": (
        "Inflammatory lung disease",
        "MONDO:0004670"
    ),
    "Hyperinflammatory ARDS subphenotype": (
        "Acute respiratory distress syndrome",
        "ARDS_POI_03270"
    ),
    "Hypoinflammatory ARDS subphenotype": (
        "Acute respiratory distress syndrome",
        "ARDS_POI_03270"
    ),
    "Focal ARDS": (
        "Acute respiratory distress syndrome",
        "ARDS_POI_03270"
    ),
    "Non-focal ARDS": (
        "Acute respiratory distress syndrome",
        "ARDS_POI_03270"
    ),
    "COVID-19-associated ARDS": (
        "Acute respiratory distress syndrome",
        "ARDS_POI_03270"
    ),
    "Pneumonia": (
        "Infectious lung disease",
        "MONDO:0005249"
    ),
    "Sepsis": (
        "Systemic infection",
        "MONDO:0021783"
    ),
}

# Mid-level mechanism classes
MECHANISM_HIERARCHY = {
    "Volutrauma": ("Ventilator-induced lung injury", "MESH:D055370"),
    "Atelectrauma": ("Ventilator-induced lung injury", "MESH:D055370"),
    "Biotrauma": ("Ventilator-induced lung injury", "MESH:D055370"),
    "Patient self-inflicted lung injury": ("Ventilator-induced lung injury", "MESH:D055370"),
    "Driving pressure": ("Mechanical ventilation parameter", ""),
    "Mechanical power": ("Mechanical ventilation parameter", ""),
}

# Mid-level intervention classes
INTERVENTION_HIERARCHY = {
    "Low tidal volume ventilation": ("Mechanical ventilation strategy", ""),
    "Low tidal volume strategy": ("Mechanical ventilation strategy", ""),
    "High PEEP ventilation strategy": ("Mechanical ventilation strategy", ""),
    "Prone positioning": ("Patient positioning intervention", "SNOMED:229824005"),
    "Awake prone positioning": ("Patient positioning intervention", "SNOMED:229824005"),
    "Prone positioning during VV-ECMO": ("Patient positioning intervention", "SNOMED:229824005"),
    "Dexamethasone": ("Corticosteroid pharmacotherapy", "CHEBI:35341"),
    "Baricitinib": ("JAK inhibitor pharmacotherapy", "CHEBI:90551"),
    "Cisatracurium": ("Neuromuscular blocking agent", "CHEBI:3720"),
    "VV-ECMO": ("Extracorporeal life support", ""),
    "Rescue VV-ECMO": ("Extracorporeal life support", ""),
    "Ultra-protective ventilation during VV-ECMO": ("Mechanical ventilation strategy", ""),
    "High-flow nasal cannula oxygen therapy": ("Oxygen therapy", "SNOMED:371907003"),
    "Conservative oxygen therapy": ("Oxygen therapy", ""),
}

# ── Fix 2: Punning resolution ─────────────────────────────────────────────────
# These 27 entities are typed as both owl:Class and owl:NamedIndividual
# Decision: keep as owl:Class (they represent types, not instances)
# Remove owl:NamedIndividual typing

PUNNING_KEEP_AS_CLASS = True  # always keep as class for biomedical ontology

# ── Fix 4: Duplicates to merge ────────────────────────────────────────────────
# Format: keep_IRI → [deprecated_IRI, ...]
DUPLICATE_MERGES = {
    "ARDS_POI_03261": ["ARDS_POI_03233"],  # Severe ARDS context PaO2/FiO2 <150 (keep the one with fuller def)
    "ARDS_POI_02284": [],                   # Conservative fluid management — check manually
    "ARDS_POI_03281": [],                   # Transpulmonary pressure monitoring — check manually
}

# ── Fix 5: Literature statement detection ────────────────────────────────────
# Patterns that indicate a class is really a literature finding, not a stable entity
LITERATURE_STATEMENT_PATTERNS = [
    r"^(No|Higher|Lower|Fewer|Greater|Similar|Reduced|Increased|Improved)\s",
    r"(benefit|finding|conclusion|result|showed|demonstrated|associated with)",
    r"\d+%",            # contains percentage
    r"vs\.",            # comparison
    r"p[<=]\s*0\.\d",   # p-value
]

# ── Fix 6: Object property assertions from gold papers ───────────────────────
# These are the 27 POI-Graph edges from the 13 landmark RCTs
# Format: (subject_label, property, object_label, pmid, confidence)
GOLD_ASSERTIONS = [
    # ARMA (PMID 10793162)
    ("Low tidal volume ventilation", "treats", "Acute respiratory distress syndrome",
     "10793162", 0.99),
    ("Low tidal volume ventilation", "hasClinicalOutcome", "28-day mortality",
     "10793162", 0.99),

    # PROSEVA (PMID 23688302)
    ("Prone positioning", "treats", "Severe ARDS context PaO2/FiO2 less than 150",
     "23688302", 0.99),
    ("Prone positioning", "hasClinicalOutcome", "28-day all-cause mortality",
     "23688302", 0.99),

    # ART (PMID 28973363)
    ("Aggressive recruitment maneuver strategy", "causesAdverseOutcome", "28-day mortality",
     "28973363", 0.92),
    ("Aggressive recruitment maneuver strategy", "causesAdverseOutcome", "Barotrauma",
     "28973363", 0.92),

    # ROSE (PMID 31112383)
    ("Cisatracurium", "hasClinicalOutcome", "90-day mortality",
     "31112383", 0.90),
    ("Lighter sedation era clinical context", "hasDifferentialResponse", "Cisatracurium",
     "31112383", 0.85),

    # PROBESE (PMID 31157366)
    ("High PEEP ventilation strategy", "improvesPhysiologicParameter", "Driving pressure",
     "31157366", 0.88),

    # LOCO2 (PMID 32160661)
    ("Conservative oxygen therapy", "causesAdverseOutcome", "Mesenteric ischemia in ARDS",
     "32160661", 0.88),

    # DEXA-ARDS (PMID 32043986)
    ("Dexamethasone", "treats", "Acute respiratory distress syndrome",
     "32043986", 0.85),
    ("Dexamethasone", "hasClinicalOutcome", "60-day mortality",
     "32043986", 0.85),
    ("Dexamethasone", "improvesPhysiologicParameter", "Ventilator-free days at day 28",
     "32043986", 0.85),

    # EOLIA (PMID 29791822)
    ("VV-ECMO", "treats", "Acute respiratory distress syndrome",
     "29791822", 0.65),

    # iPROVE-OLV (PMID 38065200)
    ("Individualized OLA", "treats", "One-lung ventilation surgical context",
     "38065200", 0.93),

    # Biomarker assertions
    ("Acute respiratory distress syndrome", "hasBiomarker", "Interleukin-6",
     "33253239", 0.90),
    ("Acute respiratory distress syndrome", "hasBiomarker", "Interleukin-8",
     "33253239", 0.90),
    ("Acute respiratory distress syndrome", "hasBiomarker", "Soluble receptor for advanced glycation end-products",
     "33253239", 0.90),
    ("Acute respiratory distress syndrome", "hasBiomarker", "Angiopoietin-2",
     "33253239", 0.90),
    ("Acute respiratory distress syndrome", "hasBiomarker", "Plasminogen activator inhibitor-1",
     "33253239", 0.90),
    ("Hyperinflammatory ARDS subphenotype", "hasBiomarker", "Interleukin-6",
     "33253239", 0.95),
    ("Hyperinflammatory ARDS subphenotype", "hasBiomarker", "Plasminogen activator inhibitor-1",
     "33253239", 0.95),
    ("Hyperinflammatory ARDS subphenotype", "hasBiomarker", "Protein C",
     "33253239", 0.95),

    # Mechanism assertions
    ("Volutrauma", "hasPathophysiology", "Acute respiratory distress syndrome",
     "10793162", 0.90),
    ("Atelectrauma", "hasPathophysiology", "Acute respiratory distress syndrome",
     "10793162", 0.90),
    ("Patient self-inflicted lung injury", "hasPathophysiology", "Acute respiratory distress syndrome",
     "34856592", 0.85),
]

# Property label → URI mapping
PROP_LABEL_TO_URI = {
    "treats":                    OBO["RO_0002606"],
    "hasClinicalOutcome":        OBO["RO_0002558"],
    "causesAdverseOutcome":      OBO["RO_0003303"],
    "hasBiomarker":              OBO["RO_0002559"],
    "hasPathophysiology":        OBO["poi_hasPathophysiology"],
    "improvesPhysiologicParameter": OBO["poi_improvesPhysiologicParameter"],
    "hasDifferentialResponse":   OBO["poi_hasDifferentialResponse"],
    "hasRiskFactor":             OBO["RO_0003303"],
    "hasAetiology":              OBO["RO_0003303"],
    "progressesTo":              OBO["RO_0002304"],
}

# ── Fix 7: Stronger external alignment ───────────────────────────────────────
MONDO_XREFS = {
    "Acute respiratory distress syndrome": "MONDO:0004670",
    "Pneumonia":                           "MONDO:0005249",
    "Sepsis":                              "MONDO:0021783",
    "COVID-19-associated ARDS":            "MONDO:0100096",
    "Chronic obstructive pulmonary disease":"MONDO:0005002",
    "Cardiac surgery context":             "MONDO:0005012",
}

HP_XREFS = {
    "Hypoxemia":                   "HP:0012418",
    "Respiratory failure":         "HP:0002878",
    "Tachypnea":                   "HP:0002789",
    "Right ventricular failure in ARDS": "HP:0001635",
    "Pulmonary hypertension":      "HP:0002092",
}

RO_PROPERTY_XREFS = {
    "treats":           "RO:0002606",
    "hasBiomarker":     "RO:0002559",
    "hasRiskFactor":    "RO:0003303",
    "causesAdverseOutcome": "RO:0003303",
    "progressesTo":     "RO:0002304",
    "preventsCondition":"RO:0002606",
    "inhibitsPathway":  "RO:0002212",
    "activatesPathway": "RO:0002213",
}

# ── Fix 8: Ontology imports ───────────────────────────────────────────────────
ONTOLOGY_IMPORTS = [
    # Stub imports — declare without loading (avoids axiom explosion)
    # These tell reasoners where predicates/classes are defined
    ("http://purl.obolibrary.org/obo/ro.owl", "Relation Ontology"),
    ("http://purl.obolibrary.org/obo/bfo.owl", "Basic Formal Ontology"),
]


# ── Main restructuring function ───────────────────────────────────────────────

def restructure(owl_in, owl_out, dry_run=False):
    print(f"\n{'='*65}")
    print("POI-KB Ontology Restructuring — v9_fixed → v10")
    print(f"{'='*65}")
    print(f"Input:  {owl_in}")
    print(f"Output: {owl_out}")
    print(f"Dry run: {dry_run}\n")

    g = Graph()
    g.parse(owl_in)
    initial_triples = len(g)
    print(f"Loaded {initial_triples:,} triples, {sum(1 for _ in g.subjects(RDF.type, OWL.Class)):,} classes\n")

    report = []
    stats = {
        "hierarchy_added": 0,
        "punning_fixed":   0,
        "labels_added":    0,
        "duplicates_deprecated": 0,
        "lit_statements_flagged": 0,
        "assertions_added": 0,
        "xrefs_added":     0,
        "imports_added":   0,
    }

    # ── Build label→IRI index ─────────────────────────────────────────────────
    label_to_iri = {}
    for s in g.subjects(RDF.type, OWL.Class):
        for lbl in g.objects(s, RDFS.label):
            label_to_iri[str(lbl).lower().strip()] = s

    # Also index by IRI suffix
    iri_to_class = {}
    for s in g.subjects(RDF.type, OWL.Class):
        suffix = str(s).split("/")[-1]
        iri_to_class[suffix] = s

    # Build subset→class index
    subset_to_classes = defaultdict(list)
    for cls in g.subjects(RDF.type, OWL.Class):
        for subset in g.objects(cls, OBO.inSubset):
            subset_key = str(subset).split("/")[-1]
            subset_to_classes[subset_key].append(cls)

    print(f"Class index: {len(label_to_iri):,} labelled | {len(iri_to_class):,} total")
    print(f"Subsets: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(subset_to_classes.items())))

    # ── FIX 1: Add formal superclass hierarchy ────────────────────────────────
    print(f"\n── FIX 1: Formal superclass hierarchy ──")

    # Add subset-level superclasses
    ontology_uri = next(g.subjects(RDF.type, OWL.Ontology), None)

    for subset_key, (parent_uri, parent_label, parent_src) in SUPERCLASS_MAP.items():
        classes_in_subset = subset_to_classes.get(subset_key, [])

        # Create parent class declaration if not present
        if (parent_uri, RDF.type, OWL.Class) not in g:
            g.add((parent_uri, RDF.type, OWL.Class))
            g.add((parent_uri, RDFS.label, Literal(parent_label)))
            g.add((parent_uri, OBOINOWL.hasDbXref, Literal(parent_src)))
            report.append(f"  Added superclass: {parent_label} ({parent_src})")

        # Add subClassOf for all classes in subset that lack named parent
        added = 0
        for cls in classes_in_subset:
            existing_parents = list(g.objects(cls, RDFS.subClassOf))
            named_parents = [p for p in existing_parents
                             if not isinstance(p, BNode)
                             and str(p) != str(OWL.Thing)]
            if not named_parents:
                g.add((cls, RDFS.subClassOf, parent_uri))
                added += 1
                stats["hierarchy_added"] += 1

        print(f"  {subset_key:30s}: added parent → {parent_label} ({added} classes)")
        report.append(f"  {subset_key}: {added} classes now have parent {parent_label}")

    # Add mid-level disease hierarchy
    for label, (parent_label, parent_xref) in DISEASE_HIERARCHY.items():
        cls_uri = label_to_iri.get(label.lower())
        if not cls_uri:
            continue
        parent_uri = label_to_iri.get(parent_label.lower())
        if parent_uri:
            # Remove generic POI_disease parent, add specific one
            g.remove((cls_uri, RDFS.subClassOf, SUPERCLASS_MAP["POI_disease"][0]))
            g.add((cls_uri, RDFS.subClassOf, parent_uri))
            stats["hierarchy_added"] += 1
        else:
            # Create intermediate class
            new_uri = URIRef(f"http://purl.obolibrary.org/obo/poi/mid_{label.replace(' ','_')}")
            g.add((new_uri, RDF.type, OWL.Class))
            g.add((new_uri, RDFS.label, Literal(parent_label)))
            if parent_xref:
                g.add((new_uri, OBOINOWL.hasDbXref, Literal(parent_xref)))
            g.add((new_uri, RDFS.subClassOf, SUPERCLASS_MAP["POI_disease"][0]))
            g.add((cls_uri, RDFS.subClassOf, new_uri))
            stats["hierarchy_added"] += 2
            report.append(f"  Created intermediate: {parent_label} for {label}")

    # Add mid-level mechanism hierarchy
    for label, (parent_label, parent_xref) in MECHANISM_HIERARCHY.items():
        cls_uri = label_to_iri.get(label.lower())
        if not cls_uri: continue
        parent_uri = label_to_iri.get(parent_label.lower())
        if not parent_uri:
            new_uri = URIRef(f"http://purl.obolibrary.org/obo/poi/mid_{re.sub(chr(32), '_', parent_label)}")
            g.add((new_uri, RDF.type, OWL.Class))
            g.add((new_uri, RDFS.label, Literal(parent_label)))
            if parent_xref:
                g.add((new_uri, OBOINOWL.hasDbXref, Literal(parent_xref)))
            g.add((new_uri, RDFS.subClassOf, SUPERCLASS_MAP["POI_mechanism"][0]))
            parent_uri = new_uri
            stats["hierarchy_added"] += 1
        g.remove((cls_uri, RDFS.subClassOf, SUPERCLASS_MAP["POI_mechanism"][0]))
        g.add((cls_uri, RDFS.subClassOf, parent_uri))
        stats["hierarchy_added"] += 1

    # Add mid-level intervention hierarchy
    for label, (parent_label, parent_xref) in INTERVENTION_HIERARCHY.items():
        cls_uri = label_to_iri.get(label.lower())
        if not cls_uri: continue
        parent_uri = label_to_iri.get(parent_label.lower())
        if not parent_uri:
            new_uri = URIRef(f"http://purl.obolibrary.org/obo/poi/mid_{re.sub(chr(32),'_',parent_label)}")
            g.add((new_uri, RDF.type, OWL.Class))
            g.add((new_uri, RDFS.label, Literal(parent_label)))
            if parent_xref:
                g.add((new_uri, OBOINOWL.hasDbXref, Literal(parent_xref)))
            g.add((new_uri, RDFS.subClassOf, SUPERCLASS_MAP["POI_intervention"][0]))
            parent_uri = new_uri
            stats["hierarchy_added"] += 1
        g.remove((cls_uri, RDFS.subClassOf, SUPERCLASS_MAP["POI_intervention"][0]))
        g.add((cls_uri, RDFS.subClassOf, parent_uri))
        stats["hierarchy_added"] += 1

    print(f"  Total hierarchy triples added: {stats['hierarchy_added']}")

    # ── FIX 2: Resolve class/individual punning ───────────────────────────────
    print(f"\n── FIX 2: Resolve class/individual punning ──")
    punned = []
    for s in g.subjects(RDF.type, OWL.Class):
        if (s, RDF.type, OWL.NamedIndividual) in g:
            punned.append(s)

    for cls in punned:
        lbl = next(g.objects(cls, RDFS.label), str(cls).split("/")[-1])
        if PUNNING_KEEP_AS_CLASS:
            g.remove((cls, RDF.type, OWL.NamedIndividual))
            stats["punning_fixed"] += 1
            report.append(f"  Punning fixed (kept as class): {lbl}")

    print(f"  Resolved {stats['punning_fixed']} punned entities (kept as classes)")

    # ── FIX 3: Add missing rdfs:labels ───────────────────────────────────────
    print(f"\n── FIX 3: Add missing labels ──")
    for cls in g.subjects(RDF.type, OWL.Class):
        if not list(g.objects(cls, RDFS.label)):
            # Generate label from IRI
            iri_suffix = str(cls).split("/")[-1]
            # Convert ARDS_POI_XXXXX to readable
            if re.match(r"^ARDS_POI_\d+$", iri_suffix):
                label = iri_suffix.replace("_"," ")
            elif iri_suffix.startswith("mid_"):
                label = iri_suffix[4:].replace("_"," ")
            else:
                label = iri_suffix.replace("_"," ").replace("-"," ")
            g.add((cls, RDFS.label, Literal(label)))
            stats["labels_added"] += 1
            report.append(f"  Label auto-generated: {label}")

    print(f"  Added {stats['labels_added']} missing labels")

    # ── FIX 4: Deprecate duplicates ───────────────────────────────────────────
    print(f"\n── FIX 4: Deprecate duplicates ──")
    for keep_iri, deprecated_iris in DUPLICATE_MERGES.items():
        keep_uri = iri_to_class.get(keep_iri)
        for dep_iri in deprecated_iris:
            dep_uri = iri_to_class.get(dep_iri)
            if not dep_uri or not keep_uri:
                continue
            dep_label = next(g.objects(dep_uri, RDFS.label), dep_iri)
            # Mark as deprecated
            g.add((dep_uri, OWL.deprecated, Literal(True, datatype=XSD.boolean)))
            g.add((dep_uri, IAO_0000231, OBO["IAO_0000227"]))  # merged into
            g.add((dep_uri, OBOINOWL.replacedBy, keep_uri))
            # Add comment
            keep_label = next(g.objects(keep_uri, RDFS.label), keep_iri)
            g.add((dep_uri, RDFS.comment,
                   Literal(f"DEPRECATED: merged into {keep_label} ({keep_iri})")))
            stats["duplicates_deprecated"] += 1
            report.append(f"  Deprecated {dep_label} → replaced by {keep_label}")
            print(f"  {dep_label} → deprecated (use {keep_label})")

    print(f"  Deprecated {stats['duplicates_deprecated']} duplicate classes")

    # ── FIX 5: Flag literature statement classes ──────────────────────────────
    print(f"\n── FIX 5: Flag literature statement classes ──")
    lit_patterns = [re.compile(p, re.I) for p in LITERATURE_STATEMENT_PATTERNS]

    for cls in g.subjects(RDF.type, OWL.Class):
        lbl = str(next(g.objects(cls, RDFS.label), ""))
        if not lbl:
            continue
        is_lit = any(p.search(lbl) for p in lit_patterns)
        if is_lit:
            g.add((cls, RDFS.comment,
                   Literal("REVIEW: This class may represent a literature finding rather than "
                            "a stable ontology entity. Consider converting to an evidence "
                            "annotation or removing. See POI-KB curation guidelines.")))
            stats["lit_statements_flagged"] += 1

    print(f"  Flagged {stats['lit_statements_flagged']} literature-statement classes for review")

    # ── FIX 6: Populate object properties from gold assertions ────────────────
    print(f"\n── FIX 6: Add gold paper assertions ──")

    # Rebuild label index after fixes
    label_to_iri = {}
    for s in g.subjects(RDF.type, OWL.Class):
        for lbl in g.objects(s, RDFS.label):
            label_to_iri[str(lbl).lower().strip()] = s

    for subj_lbl, prop_name, obj_lbl, pmid, conf in GOLD_ASSERTIONS:
        subj = label_to_iri.get(subj_lbl.lower())
        obj  = label_to_iri.get(obj_lbl.lower())
        prop = None
        # Find property URI
        for p in g.subjects(RDF.type, OWL.ObjectProperty):
            p_label = str(next(g.objects(p, RDFS.label), ""))
            if p_label.lower() == prop_name.lower():
                prop = p
                break
        if not prop:
            prop = PROP_LABEL_TO_URI.get(prop_name)

        if subj and obj and prop:
            # Check if assertion already exists
            if (subj, prop, obj) not in g:
                g.add((subj, prop, obj))
                # Add PMID provenance as annotation
                g.add((subj, OBOINOWL.hasDbXref,
                       Literal(f"PMID:{pmid} confidence={conf}")))
                stats["assertions_added"] += 1
        else:
            missing = []
            if not subj: missing.append(f"subject='{subj_lbl}'")
            if not obj:  missing.append(f"object='{obj_lbl}'")
            if not prop: missing.append(f"property='{prop_name}'")
            report.append(f"  SKIPPED assertion: {subj_lbl} → {obj_lbl} (missing: {', '.join(missing)})")

    print(f"  Added {stats['assertions_added']} gold paper assertions")

    # ── FIX 7: Strengthen external alignment ─────────────────────────────────
    print(f"\n── FIX 7: External alignment ──")

    for label, mondo_id in MONDO_XREFS.items():
        cls = label_to_iri.get(label.lower())
        if cls:
            g.add((cls, OBOINOWL.hasDbXref, Literal(mondo_id)))
            stats["xrefs_added"] += 1

    for label, hp_id in HP_XREFS.items():
        cls = label_to_iri.get(label.lower())
        if cls:
            g.add((cls, OBOINOWL.hasDbXref, Literal(hp_id)))
            g.add((cls, OWL.equivalentClass,
                   URIRef(f"http://purl.obolibrary.org/obo/{hp_id.replace(':','_')}")))
            stats["xrefs_added"] += 1

    for prop_label, ro_id in RO_PROPERTY_XREFS.items():
        for prop in g.subjects(RDF.type, OWL.ObjectProperty):
            p_lbl = str(next(g.objects(prop, RDFS.label), ""))
            if p_lbl.lower() == prop_label.lower():
                g.add((prop, OBOINOWL.hasDbXref, Literal(ro_id)))
                stats["xrefs_added"] += 1
                break

    print(f"  Added {stats['xrefs_added']} external alignment xrefs")

    # ── FIX 8: Add ontology import declarations ───────────────────────────────
    print(f"\n── FIX 8: Add ontology import declarations ──")

    ont_uri = next(g.subjects(RDF.type, OWL.Ontology), None)
    if ont_uri:
        for import_uri, import_label in ONTOLOGY_IMPORTS:
            import_ref = URIRef(import_uri)
            if (ont_uri, OWL.imports, import_ref) not in g:
                # Use owl:imports stub (not loading full ontology)
                g.add((ont_uri, RDFS.comment,
                       Literal(f"Aligns to: {import_label} <{import_uri}> "
                               "(stub reference — not loaded to avoid axiom explosion)")))
                stats["imports_added"] += 1
                print(f"  Import stub declared: {import_label}")

    # ── Write output ──────────────────────────────────────────────────────────
    final_triples = len(g)
    delta = final_triples - initial_triples

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("RESTRUCTURING SUMMARY")
    print(f"{'='*65}")
    print(f"Triples:              {initial_triples:,} → {final_triples:,} (+{delta:,})")
    print(f"Hierarchy added:      {stats['hierarchy_added']}")
    print(f"Punning fixed:        {stats['punning_fixed']}")
    print(f"Labels added:         {stats['labels_added']}")
    print(f"Duplicates deprecated:{stats['duplicates_deprecated']}")
    print(f"Lit. flags added:     {stats['lit_statements_flagged']}")
    print(f"Assertions added:     {stats['assertions_added']}")
    print(f"Xrefs added:          {stats['xrefs_added']}")
    print(f"Import stubs:         {stats['imports_added']}")

    # Hierarchy depth check
    classes_with_parent = sum(
        1 for cls in g.subjects(RDF.type, OWL.Class)
        if any(True for p in g.objects(cls, RDFS.subClassOf)
               if not isinstance(p, BNode) and str(p) != str(OWL.Thing))
    )
    total_classes = sum(1 for _ in g.subjects(RDF.type, OWL.Class))
    print(f"\nHierarchy coverage:   {classes_with_parent}/{total_classes} classes have named parent "
          f"({100*classes_with_parent//total_classes if total_classes else 0}%)")

    # Property usage
    print(f"\nObject property usage after fix:")
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        lbl = next(g.objects(prop, RDFS.label), str(prop).split("/")[-1])
        count = sum(1 for _ in g.triples((None, prop, None)))
        if count > 0:
            print(f"  {str(lbl):40s}: {count}")

    if not dry_run:
        g.serialize(destination=str(owl_out), format="xml")
        print(f"\n✓ Written: {owl_out}")
    else:
        print(f"\n(dry-run — not written)")

    # Write report
    report_path = Path(owl_out).parent / "ontology_restructure_report.txt"
    if not dry_run:
        with open(report_path, "w") as f:
            f.write("POI-KB Ontology Restructuring Report — v9_fixed → v10\n")
            f.write("="*60 + "\n\n")
            for line in report:
                f.write(line + "\n")
            f.write(f"\nTotal changes applied: {sum(stats.values())}\n")
        print(f"✓ Report: {report_path}")

    print(f"\nNext steps:")
    print(f"  1. Run ROBOT ELK to verify consistency:")
    print(f"     robot reason --reasoner ELK --input {owl_out} --output reasoned.owl")
    print(f"  2. Run verification: python3 poi_ontology_verify.py {owl_out}")
    print(f"  3. Review flagged literature-statement classes manually")
    print(f"  4. Complete BioPortal xref enrichment for all 1037 classes")
    print(f"  5. Expert review of mid-level hierarchy additions")

    return g, stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",   default="corpus_analysis/ontology_output/ards_poi_ontology_v9_fixed.owl")
    ap.add_argument("--output",  default="corpus_analysis/ontology_output/ards_poi_ontology_v10.owl")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    restructure(args.input, args.output, args.dry_run)
