#!/usr/bin/env python3
"""The gene panel this dataset measures, as real HGNC symbols.

Every symbol below is a real human gene. That matters more here than the size of
the panel: the ported notebooks flag mitochondrial genes by the `MT-` prefix and
ribosomal ones by `RPS`, and `02_regulatory_activity.ipynb` scores
transcription-factor activity against CollecTRI, which is indexed by symbol. A
panel of invented names would run and score nothing, which is a demonstration
that proves the opposite of what it looks like.

So the panel is CURATED rather than generated, and it is therefore a targeted
panel — about seven hundred genes, the size of a commercial targeted immune
panel — and not a whole transcriptome. `README.md` says what follows from that.
"""

from __future__ import annotations

# The thirteen protein-coding mitochondrial genes. `rsc.pp.flag_gene_family(…,
# gene_family_prefix="MT")` finds exactly these, and `pct_counts_MT` is the QC
# axis both preprocessing notebooks filter on.
MITOCHONDRIAL = [
    'MT-ND1', 'MT-ND2', 'MT-CO1', 'MT-CO2', 'MT-ATP8', 'MT-ATP6', 'MT-CO3',
    'MT-ND3', 'MT-ND4L', 'MT-ND4', 'MT-ND5', 'MT-ND6', 'MT-CYB',
]

# Small-subunit ribosomal proteins — the `RPS` prefix the notebooks flag as
# RIBO — and the large-subunit ones beside them, which they do not flag but
# which are the other half of what dominates a count matrix.
RIBOSOMAL = [
    'RPS2', 'RPS3', 'RPS3A', 'RPS4X', 'RPS4Y1', 'RPS5', 'RPS6', 'RPS7', 'RPS8',
    'RPS9', 'RPS10', 'RPS11', 'RPS12', 'RPS13', 'RPS14', 'RPS15', 'RPS15A',
    'RPS16', 'RPS17', 'RPS18', 'RPS19', 'RPS20', 'RPS21', 'RPS23', 'RPS24',
    'RPS25', 'RPS26', 'RPS27', 'RPS27A', 'RPS28', 'RPS29', 'RPSA',
    'RPL3', 'RPL4', 'RPL5', 'RPL6', 'RPL7', 'RPL7A', 'RPL8', 'RPL9', 'RPL10',
    'RPL10A', 'RPL11', 'RPL12', 'RPL13', 'RPL13A', 'RPL14', 'RPL15', 'RPL17',
    'RPL18', 'RPL18A', 'RPL19', 'RPL21', 'RPL22', 'RPL23', 'RPL23A', 'RPL24',
    'RPL26', 'RPL27', 'RPL27A', 'RPL28', 'RPL29', 'RPL30', 'RPL31', 'RPL32',
    'RPL34', 'RPL35', 'RPL35A', 'RPL36', 'RPL36A', 'RPL37', 'RPL37A', 'RPL38',
    'RPL39', 'RPL41', 'RPLP0', 'RPLP1', 'RPLP2',
]

# The antigen-presentation locus. Class II is a monocyte, dendritic and B-cell
# program, which is what makes it worth carrying on a panel meant to separate
# those lineages.
HLA = [
    'HLA-A', 'HLA-B', 'HLA-C', 'HLA-E', 'HLA-F', 'HLA-G',
    'HLA-DRA', 'HLA-DRB1', 'HLA-DRB5', 'HLA-DQA1', 'HLA-DQA2', 'HLA-DQB1',
    'HLA-DPA1', 'HLA-DPB1', 'HLA-DMA', 'HLA-DMB', 'HLA-DOA', 'HLA-DOB',
    'CD74', 'B2M', 'TAP1', 'TAP2', 'TAPBP', 'PSMB8', 'PSMB9', 'NLRC5', 'CIITA',
]

# Surface antigens, which is how a flow cytometrist would name the same cells.
SURFACE = [
    'CD1C', 'CD2', 'CD3D', 'CD3E', 'CD3G', 'CD4', 'CD5', 'CD6', 'CD7', 'CD8A',
    'CD8B', 'CD9', 'CD14', 'CD19', 'CD22', 'CD24', 'CD27', 'CD28', 'CD33',
    'CD34', 'CD36', 'CD37', 'CD38', 'CD40', 'CD40LG', 'CD44', 'CD47', 'CD48',
    'CD52', 'CD55', 'CD58', 'CD59', 'CD63', 'CD68', 'CD69', 'CD70', 'CD79A',
    'CD79B', 'CD81', 'CD82', 'CD83', 'CD84', 'CD86', 'CD99', 'CD160', 'CD163',
    'CD177', 'CD180', 'CD200', 'CD209', 'CD226', 'CD244', 'CD247', 'CD274',
    'CD276', 'CD300A', 'CD300C', 'CD302', 'CD320', 'CD3EAP',
    'ITGA2B', 'ITGA4', 'ITGAL', 'ITGAM', 'ITGAX', 'ITGB1', 'ITGB2', 'ITGB7',
    'SELL', 'SELPLG', 'PECAM1', 'ICAM1', 'ICAM2', 'ICAM3', 'VCAM1', 'L1CAM',
    'PTPRC', 'THY1', 'ENTPD1', 'NT5E', 'SLAMF1', 'SLAMF6', 'SLAMF7',
    'TNFRSF1A', 'TNFRSF1B', 'TNFRSF4', 'TNFRSF9', 'TNFRSF13B', 'TNFRSF13C',
    'TNFRSF17', 'TNFRSF18', 'TNFSF10', 'TNFSF13B', 'FAS', 'FASLG',
    'PDCD1', 'CTLA4', 'LAG3', 'HAVCR2', 'TIGIT', 'BTLA', 'ICOS', 'ICOSLG',
]

# Transcription factors, and the reason the panel is worth scoring against
# CollecTRI at all. GATA3 is on it deliberately: it is the gene
# `02_regulatory_activity.ipynb` plots by name.
FACTORS = [
    'GATA1', 'GATA2', 'GATA3', 'TBX21', 'RORA', 'RORC', 'FOXP3', 'FOXO1',
    'FOXP1', 'IKZF1', 'IKZF2', 'IKZF3', 'STAT1', 'STAT2', 'STAT3', 'STAT4',
    'STAT5A', 'STAT5B', 'STAT6', 'IRF1', 'IRF2', 'IRF3', 'IRF4', 'IRF5',
    'IRF7', 'IRF8', 'IRF9', 'NFKB1', 'NFKB2', 'NFKBIA', 'NFKBIB', 'REL',
    'RELA', 'RELB', 'JUN', 'JUNB', 'JUND', 'FOS', 'FOSB', 'FOSL2', 'EGR1',
    'EGR2', 'EGR3', 'KLF2', 'KLF4', 'KLF6', 'KLF10', 'KLF13', 'MYC', 'MAX',
    'MXD4', 'RUNX1', 'RUNX2', 'RUNX3', 'TCF7', 'TCF4', 'LEF1', 'BACH1',
    'BACH2', 'PAX5', 'EBF1', 'SPI1', 'SPIB', 'CEBPA', 'CEBPB', 'CEBPD',
    'ETS1', 'ETS2', 'ELK1', 'ELK4', 'ELF1', 'ELF4', 'GABPA', 'YY1', 'SP1',
    'SP3', 'E2F1', 'E2F2', 'E2F3', 'E2F4', 'E2F5', 'MYB', 'MYBL2', 'BCL11A',
    'BCL11B', 'BCL6', 'ZEB1', 'ZEB2', 'HIF1A', 'EPAS1', 'ARNT', 'PPARG',
    'NR4A1', 'NR4A2', 'NR4A3', 'RARA', 'RXRA', 'VDR', 'NR3C1', 'CREB1',
    'CREM', 'ATF1', 'ATF2', 'ATF3', 'ATF4', 'XBP1', 'SREBF1', 'SREBF2',
    'MITF', 'TFEB', 'NFE2L2', 'MAF', 'MAFB', 'MAFF', 'MAFG', 'BATF', 'BATF3',
    'PRDM1', 'ID2', 'ID3', 'TOX', 'EOMES', 'ZNF683', 'ZBTB7A', 'ZBTB16',
    'ZBTB32', 'ZEB2', 'TFDP1', 'SMAD3', 'SMAD4', 'SMAD7', 'NOTCH1', 'NOTCH2',
    'RBPJ', 'HES1', 'HEY1', 'TAL1', 'LYL1', 'LMO2', 'MEF2C', 'MEF2D', 'NFATC1',
    'NFATC2', 'NFATC3', 'NFIL3', 'AHR', 'ARID5A', 'ARID5B', 'SATB1', 'HMGB1',
    'HMGB2', 'TP53', 'MDM2', 'CDKN1A', 'CDKN1B', 'RB1', 'CCND2', 'CCND3',
]

# Cytokines, chemokines and their receptors. The axis along which two cells of
# the same type, from two donors, are genuinely different.
SIGNALLING = [
    'IL1B', 'IL1RN', 'IL2', 'IL2RA', 'IL2RB', 'IL2RG', 'IL4', 'IL4R', 'IL5',
    'IL6', 'IL6R', 'IL6ST', 'IL7', 'IL7R', 'IL10', 'IL10RA', 'IL12A', 'IL12B',
    'IL13', 'IL15', 'IL16', 'IL17A', 'IL17RA', 'IL18', 'IL18R1', 'IL21',
    'IL21R', 'IL23A', 'IL27', 'IL32', 'IFNG', 'IFNGR1', 'IFNGR2', 'IFNAR1',
    'IFNAR2', 'TNF', 'LTA', 'LTB', 'TGFB1', 'TGFBR1', 'TGFBR2', 'CSF1',
    'CSF1R', 'CSF2', 'CSF2RA', 'CSF2RB', 'CSF3R', 'KIT', 'FLT3', 'FLT3LG',
    'CCL2', 'CCL3', 'CCL3L1', 'CCL4', 'CCL4L2', 'CCL5', 'CCL17', 'CCL19',
    'CCL20', 'CCL22', 'CCR1', 'CCR2', 'CCR4', 'CCR5', 'CCR6', 'CCR7', 'CCR10',
    'CXCL1', 'CXCL2', 'CXCL8', 'CXCL9', 'CXCL10', 'CXCL13', 'CXCL16',
    'CXCR1', 'CXCR2', 'CXCR3', 'CXCR4', 'CXCR5', 'CXCR6', 'CX3CR1', 'XCL1',
    'XCL2', 'S1PR1', 'S1PR4', 'ADORA2A', 'P2RX7', 'P2RY8',
    'JAK1', 'JAK2', 'JAK3', 'TYK2', 'SOCS1', 'SOCS3', 'CISH', 'PTPN2',
    'PTPN6', 'PTPN11', 'PTPN22', 'LCK', 'ZAP70', 'ITK', 'LAT', 'LAT2', 'FYN',
    'SYK', 'BTK', 'BLNK', 'PLCG1', 'PLCG2', 'PRKCB', 'PRKCQ', 'CARD11',
    'MALT1', 'BCL10', 'VAV1', 'SKAP1', 'THEMIS', 'TRAT1', 'UBASH3A',
]

# Lineage programmes. The eight cell types this matrix carries are separable by
# these and almost nothing else, which is deliberate — a demonstration corpus
# whose clusters cannot be named is one nobody can check.
LINEAGE = [
    # T and NK
    'TRAC', 'TRBC1', 'TRBC2', 'TRDC', 'TRGC1', 'TRGC2', 'CCL5', 'IL32',
    'NKG7', 'GNLY', 'PRF1', 'GZMA', 'GZMB', 'GZMH', 'GZMK', 'GZMM', 'KLRB1',
    'KLRC1', 'KLRD1', 'KLRF1', 'KLRG1', 'NCAM1', 'NCR3', 'FGFBP2', 'CST7',
    'CTSW', 'SPON2', 'TYROBP', 'FCER1G', 'HOPX', 'KLRK1', 'SH2D1B',
    'LDHB', 'NOSIP', 'PIK3IP1', 'MAL', 'TRABD2A', 'LTB', 'AQP3', 'TNFAIP3',
    # B and plasma
    'MS4A1', 'TCL1A', 'BANK1', 'BLK', 'VPREB3', 'FCRL1', 'FCRL2', 'FCRL3',
    'FCRL5', 'POU2AF1', 'MZB1', 'JCHAIN', 'DERL3', 'SSR4', 'SEC11C',
    'IGHM', 'IGHD', 'IGHG1', 'IGHG3', 'IGHA1', 'IGKC', 'IGLC1', 'IGLC2',
    'IGLC3', 'LINC00926', 'RALGPS2', 'TNFRSF13B',
    # Monocyte and dendritic
    'LYZ', 'S100A8', 'S100A9', 'S100A12', 'FCN1', 'VCAN', 'MNDA', 'CST3',
    'FCER1A', 'CLEC10A', 'CLEC4A', 'CLEC4E', 'CLEC7A', 'CLEC12A', 'FCGR1A',
    'FCGR2A', 'FCGR2B', 'FCGR3A', 'FCGR3B', 'MS4A7', 'MS4A4A', 'MS4A6A',
    'LST1', 'AIF1', 'COTL1', 'SERPINA1', 'CTSS', 'CTSD', 'CTSB', 'CTSH',
    'TYMP', 'IFI30', 'GRN', 'NPC2', 'LGALS1', 'LGALS2', 'LGALS3', 'LGALS9',
    'MPEG1', 'TREM1', 'TLR2', 'TLR4', 'TLR7', 'TLR8', 'NLRP3', 'CASP1',
    'ASGR1', 'ASGR2', 'VMO1', 'LILRA1', 'LILRA5', 'LILRB1', 'LILRB2',
    'LILRB4', 'SIGLEC7', 'SIGLEC9', 'SIGLEC10', 'IRF2BP2',
    'JAML', 'PLBD1', 'RETN', 'ALOX5AP', 'ANXA1', 'ANXA2', 'ANXA5',
    # Granulocyte granule content, which a monocyte preparation always carries
    'MPO', 'ELANE', 'PRTN3', 'CTSG', 'DEFA3', 'DEFA4', 'CAMP', 'LCN2',
    'BPI', 'CEACAM8', 'LTF', 'OLFM4', 'SLPI', 'CHI3L1',
    # Platelet
    'PPBP', 'PF4', 'GP9', 'GP1BA', 'TUBB1', 'NRGN', 'CAVIN2', 'CLU',
    'SPARC', 'MYL9', 'TAGLN2', 'TMEM40', 'HIST1H2AC', 'ACRBP', 'RGS18',
    'THBS1', 'VWF', 'SELP', 'TREML1',
    # Interferon response, which separates two donors rather than two lineages
    'ISG15', 'IFI6', 'IFI44L', 'IFIT1', 'IFIT2', 'IFIT3', 'IFITM1', 'IFITM2',
    'IFITM3', 'MX1', 'MX2', 'OAS1', 'OAS2', 'OAS3', 'OASL', 'RSAD2', 'STAT1',
    'EPSTI1', 'LY6E', 'XAF1', 'SIGLEC1', 'USP18', 'HERC5', 'SAMD9L',
]

# Housekeeping and metabolism — most of a count matrix, and the part a batch
# effect shows up in most clearly.
HOUSEKEEPING = [
    'ACTB', 'ACTG1', 'GAPDH', 'TUBA1A', 'TUBA1B', 'TUBA4A', 'TUBB', 'TUBB4B',
    'TPT1', 'EEF1A1', 'EEF1B2', 'EEF1D', 'EEF1G', 'EEF2', 'NACA', 'BTF3',
    'UBA52', 'UBB', 'UBC', 'UBE2D3', 'PABPC1', 'HSP90AA1', 'HSP90AB1',
    'HSPA5', 'HSPA8', 'HSPB1', 'HSPD1', 'DNAJA1', 'DNAJB1', 'CALM1', 'CALM2',
    'CALM3', 'CALR', 'CANX', 'PDIA3', 'PDIA6', 'P4HB', 'SRP9', 'SRP14',
    'SEC61B', 'SEC61G', 'SSR2', 'SSR3', 'SPCS1', 'SPCS2', 'SPCS3',
    'ATP5F1A', 'ATP5F1B', 'ATP5F1C', 'ATP5F1E', 'ATP5MC1', 'ATP5MC2',
    'ATP5MC3', 'ATP5PB', 'ATP5PD', 'ATP5PF', 'ATP6V0B', 'ATP6V0C', 'ATP6V1F',
    'NDUFA1', 'NDUFA2', 'NDUFA4', 'NDUFA6', 'NDUFA11', 'NDUFA13', 'NDUFB1',
    'NDUFB2', 'NDUFB4', 'NDUFB9', 'NDUFB11', 'NDUFS5', 'NDUFS6',
    'COX4I1', 'COX5A', 'COX5B', 'COX6A1', 'COX6B1', 'COX6C', 'COX7A2',
    'COX7B', 'COX7C', 'COX8A', 'UQCRB', 'UQCRH', 'UQCRQ', 'UQCR10', 'UQCR11',
    'SDHB', 'SDHD', 'CYC1', 'CYCS', 'SLC25A3', 'SLC25A5', 'SLC25A6',
    'VDAC1', 'VDAC2', 'VDAC3', 'TOMM7', 'TIMM13', 'TMEM258',
    'ENO1', 'PGK1', 'PKM', 'LDHA', 'LDHB', 'TPI1', 'ALDOA', 'PGAM1', 'GPI',
    'PFKL', 'HK1', 'SLC2A3', 'G6PD', 'TALDO1', 'TKT', 'PGD',
    'MYL6', 'MYL12A', 'MYL12B', 'TMSB4X', 'TMSB10', 'PFN1', 'CFL1', 'ARPC1B',
    'ARPC2', 'ARPC3', 'ARPC5', 'ACTR2', 'ACTR3', 'CAPZA1', 'CAPZB', 'GSN',
    'VIM', 'TAGLN2', 'CORO1A', 'MSN', 'EZR', 'RAC2', 'RHOA', 'CDC42',
    'YWHAB', 'YWHAE', 'YWHAZ', 'SOD1', 'SOD2', 'PRDX1', 'PRDX2', 'PRDX5',
    'PRDX6', 'TXN', 'TXN2', 'GPX1', 'GPX4', 'GSTP1', 'MGST3',
    'NPM1', 'NCL', 'HNRNPA1', 'HNRNPA2B1', 'HNRNPC', 'HNRNPK', 'HNRNPU',
    'SRSF2', 'SRSF5', 'SRSF7', 'PTBP1', 'DDX5', 'DDX17', 'EIF1', 'EIF3E',
    'EIF3F', 'EIF3H', 'EIF3L', 'EIF4A1', 'EIF4A2', 'EIF4B', 'EIF4G2',
    'EIF5A', 'ETF1', 'PPIA', 'PPIB', 'FKBP1A', 'PEBP1', 'SERF2', 'SOD1',
    'MYDGF', 'OAZ1', 'ODC1', 'SNRPB', 'SNRPD2', 'SNRPE', 'SNRPG', 'LSM7',
    'PSMA7', 'PSMB1', 'PSMB2', 'PSMB4', 'PSMB6', 'PSMD8', 'PSME1', 'PSME2',
    'RAB1B', 'RAB7A', 'RAB11A', 'RAB11B', 'ARF1', 'ARF4', 'ARPC4', 'COPE',
    'SAR1A', 'TMED2', 'TMED9', 'GDI2', 'VAMP8', 'SNX3', 'CD63',
    'FTH1', 'FTL', 'TFRC', 'SLC11A2', 'ALAS1', 'HMOX1', 'BLVRB',
    'MALAT1', 'NEAT1', 'SNHG5', 'SNHG8', 'GAS5', 'ZFAS1',
    'BCL2', 'BCL2A1', 'BCL2L1', 'BCL2L11', 'MCL1', 'BAX', 'BID', 'CASP3',
    'CASP8', 'XIAP', 'BIRC3', 'TNFAIP8', 'GIMAP4', 'GIMAP7',
    'JUNB', 'DUSP1', 'DUSP2', 'DUSP4', 'ZFP36', 'ZFP36L1', 'ZFP36L2',
    'KLF6', 'TSC22D3', 'TXNIP', 'SLC38A1', 'SLC3A2', 'SLC7A5', 'SLC25A37',
]


def panel() -> list[str]:
    """The panel, de-duplicated and in a stable order.

    Order is the order the families are written above, which groups genes the
    way somebody reading `sc_genes` would expect. `sc_workspace` indexes the
    matrix by `sc_genes` order, so this is also the var order of every AnnData
    assembled from this dataset.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for family, names in FAMILIES:
        for name in names:
            if name not in seen:
                seen.add(name)
                ordered.append(name)
    return ordered


def family_of(gene: str) -> str:
    """Which family a gene was carried on the panel for — `sc_genes.panel_class`."""
    for family, names in FAMILIES:
        if gene in names:
            return family
    raise KeyError(gene)


FAMILIES: list[tuple[str, list[str]]] = [
    ('mitochondrial', MITOCHONDRIAL),
    ('ribosomal', RIBOSOMAL),
    ('antigen-presentation', HLA),
    ('surface', SURFACE),
    ('transcription-factor', FACTORS),
    ('signalling', SIGNALLING),
    ('lineage', LINEAGE),
    ('housekeeping', HOUSEKEEPING),
]


if __name__ == '__main__':
    genes = panel()
    print(f'{len(genes)} genes')
    for family, _ in FAMILIES:
        print(f'  {family:22} {sum(1 for g in genes if family_of(g) == family):4}')
