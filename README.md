# POI-KB-ARDS
**An Evidence-Anchored Biomedical Knowledge Graph Bridging the Research-to-Practice Gap in Perioperative ARDS**

[![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
[![NIH R24HL180372](https://img.shields.io/badge/NIH-R24HL180372-blue)](https://reporter.nih.gov/)
[![OWL ELK Consistent](https://img.shields.io/badge/OWL-ELK%20Consistent-green)](#ontology)
[![BioLink Aligned](https://img.shields.io/badge/BioLink-Aligned-teal)](#knowledge-graph)

## Overview

POI-KB-ARDS is the ARDS pilot of the **Perioperative Organ Injury Knowledge Base (POI-KB)**,
an NIH-funded platform bridging the 17-year research-to-practice gap in perioperative organ injury care.

Built by an 8-agent AI pipeline from 416 curated ARDS clinical trial abstracts (2000–2023).

| Component | Stats |
|-----------|-------|
| **Ontology v10** | 1,051 classes · 7,186 triples · 26 object properties · 99% hierarchy · ROBOT ELK ✓ |
| **Knowledge Graph v1** | 518 nodes · 341 evidence edges · 292 unique PMIDs · 11 landmark trials |
| **Evaluation** | 6 systems × 7 dimensions · N=416 papers · Haiku D3 F1=0.800 (best) |

**Grant:** NIH R24HL180372
**Institution:** UTHealth Houston School of Biomedical Informatics + MD Anderson Cancer Center

---

## Repository Structure
poi_kb_ards/
├── ontology/
│   ├── ards_poi_ontology_v10.owl          # Production OWL — ROBOT ELK consistent
│   ├── ards_poi_ontology_v10_expert.owl   # xref-enriched version
│   └── ards_poi_nodes_v4_expert.csv       # Flat class table
├── knowledge_graph/
│   ├── poi_graph_nodes.csv                # 518 nodes with BioLink node_type
│   ├── poi_graph_edges.csv                # 341 evidence edges with confidence
│   └── poi_graph.ttl                      # RDF Turtle with provenance
├── evaluation/
│   ├── comparison_table.tsv               # 6-system × 7-dimension results
│   └── gold_standard/                     # 15 expert-curated records
└── scripts/
├── poi_ontology_restructure.py
├── poi_ontology_verify.py
└── poi_graph_builder.py
---

## Quick Start

```bash
git clone https://github.com/wangjl99/poi_kb_ards.git
cd poi_kb_ards
pip install -r scripts/requirements.txt
python3 scripts/poi_ontology_verify.py ontology/ards_poi_ontology_v10.owl
# Expected: ALL CHECKS PASSED (10/10)
```

---

## Evaluation Results

| System | D1 Concept | D3 Relation | D5 Semantic | Composite |
|--------|-----------|------------|------------|-----------|
| **Haiku (POI-KB)** | 0.558 | **0.800** | 0.697 | 0.467 |
| GPT-4o zero-shot | 0.615 | 0.667 | 0.654 | 0.444 |
| **OntoGPT/GPT-4o** | **0.738** | 0.733 | **0.829** | **0.548** |
| REBEL (baseline) | 0.358 | 0.467 | 0.409 | 0.187 |

**Key finding:** Domain-specific prompting of Claude Haiku achieves best relation type
classification (D3=0.800), outperforming GPT-5.4 on clinical semantics.

---

## Citation

```bibtex
@misc{poikb_ards_2026,
  title   = {{POI-KB-ARDS}: An Evidence-Anchored Biomedical Knowledge Graph},
  author  = {Wang, Jinlian and Liang, Yafen and Liu, Hongfang and Eltzschig, Holger K.},
  year    = {2026},
  url     = {https://github.com/wangjl99/poi_kb_ards},
  note    = {NIH R24HL180372. UTHealth Houston.}
}
```

## License

CC BY 4.0 — see [LICENSE](LICENSE)

## Contact

**PI:** Dr. Jinlian Wang — UTHealth Houston School of Biomedical Informatics
**Grant:** NIH R24HL180372
