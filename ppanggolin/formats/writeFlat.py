#!/usr/bin/env python3
#coding:utf-8

#default libraries
import argparse
from multiprocessing import Pool
from collections import Counter, defaultdict
import logging
import pkg_resources
from statistics import median
import os

#local libraries
from ppanggolin.pangenome import Pangenome
from ppanggolin.utils import write_compressed_or_not, mkOutdir
from ppanggolin.formats import checkPangenomeInfo, getGeneSequencesFromFile

#installed libraries
from tqdm import tqdm

#global variable to store the pangenome
pan = None

def writeJSONheader(json):
    json.write('{"directed": false, "multigraph": false,')
    json.write(' "graph": {')
    json.write(' "organisms": {')
    orgstr = []
    for org in pan.organisms:
        orgstr.append('"'+org.name+'": {')
        contigstr = []
        for contig in org.contigs:
            contigstr.append('"' + contig.name + '": {"is_circular": ' + ('true' if contig.is_circular else 'false')+'}')
        orgstr[-1] += ', '.join(contigstr) + "}"

    json.write(', '.join(orgstr) + "}")
    ##if other things are to be written such as the parameters, write them here
    json.write('},')

def writeJSONGeneFam(geneFam, json):
        json.write('{'+ f'"id": "{geneFam.name}", "nb_genes": {len(geneFam.genes)}, "partition": "{geneFam.namedPartition}", "subpartition": "{geneFam.partition}"')
        orgDict = {}
        name_counts = Counter()
        product_counts = Counter()
        length_counts = Counter()
        for gene in geneFam.genes:
            name_counts[gene.name] += 1
            product_counts[gene.product] += 1
            length_counts[gene.stop - gene.start] += 1
            try:
                orgDict[gene.organism][gene.contig].append(gene)
            except KeyError:
                try:
                    orgDict[gene.organism][gene.contig] = [gene]
                except KeyError:
                    orgDict[gene.organism] = {gene.contig : [gene]}
        json.write(f', "name": "{name_counts.most_common(1)[0][0]}", "product": "{product_counts.most_common(1)[0][0]}", "length": {length_counts.most_common(1)[0][0]}')
        json.write(', "organisms": {')
        orgstr = []
        for org in orgDict:
            orgstr.append('"' + org.name + '": {')
            contigstr = []
            for contig in orgDict[org]:
                contigstr.append('"' + contig.name + '": {')
                genestr = []
                for gene in orgDict[org][contig]:
                    genestr.append('"' + gene.ID + '": {' + f'"name": "{gene.name}", "product": "{gene.product}", "is_fragment": {"true" if gene.is_fragment else "false"}, "position": {gene.position}, "strand": "{gene.strand}", "end": {gene.stop}, "start": {gene.start}'+'}')
                contigstr[-1] += ", ".join(genestr) + "}"
            orgstr[-1] += ", ".join(contigstr) + "}"
        json.write(", ".join(orgstr) + "}}")

def writeJSONnodes(json):
    json.write('"nodes": [')
    famList = list(pan.geneFamilies)
    firstFam = famList[0]
    writeJSONGeneFam(firstFam, json)
    for geneFam in famList[1:]:
        json.write(', ')
        writeJSONGeneFam(geneFam, json)
    json.write(']')

def writeJSONedge(edge, json):
    json.write("{")
    json.write(f'"weight": {len(edge.genePairs)}, "source": "{edge.source.name}", "target": "{edge.target.name}"')
    json.write(', "organisms": {')
    orgstr = []
    for org in edge.getOrgDict():
        orgstr.append('"' + org.name + '": [')
        genepairstr = []
        for genepair in  edge.getOrgDict()[org]:
            genepairstr.append('{"source": "' + genepair[0].ID + '", "target": "' + genepair[1].ID + f'", "length": {genepair[0].start - genepair[1].stop}' + '}')
        orgstr[-1] += ', '.join(genepairstr) + ']'
    json.write(', '.join(orgstr) + "}}")

def writeJSONedges(json):
    json.write(', "links": [')
    edgelist = list(pan.edges)
    writeJSONedge(edgelist[0], json)
    for edge in edgelist[1:]:
        json.write(", ")
        writeJSONedge(edge, json)
    json.write(']')

def writeJSON(output, compress):
    logging.getLogger().info("Writing the json file for the pangenome graph...")
    outname = output + "/pangenomeGraph.json"
    with write_compressed_or_not(outname, compress) as json:
        writeJSONheader(json)
        writeJSONnodes(json)
        writeJSONedges(json)
        json.write("}")
    logging.getLogger().info(f"Done writing the json file : '{outname}'")

def writeGEXFheader(gexf, light):
    if not light:
        index = pan.getIndex()#has been computed already
    gexf.write('<?xml version="1.1" encoding="UTF-8"?>\n<gexf xmlns:viz="http://www.gexf.net/1.2draft/viz" xmlns="http://www.gexf.net/1.2draft" version="1.2">\n')
    gexf.write('  <graph mode="static" defaultedgetype="undirected">\n')
    gexf.write('    <attributes class="node" mode="static">\n')
    gexf.write('      <attribute id="0" title="nb_genes" type="long" />\n')
    gexf.write('      <attribute id="1" title="name" type="string" />\n')
    gexf.write('      <attribute id="2" title="product" type="string" />\n')
    gexf.write('      <attribute id="3" title="type" type="string" />\n')
    gexf.write('      <attribute id="4" title="partition" type="string" />\n')
    gexf.write('      <attribute id="5" title="subpartition" type="string" />\n')
    gexf.write('      <attribute id="6" title="partition_exact" type="string" />\n')
    gexf.write('      <attribute id="7" title="partition_soft" type="string" />\n')
    gexf.write('      <attribute id="8" title="length_avg" type="double" />\n')
    gexf.write('      <attribute id="9" title="length_med" type="long" />\n')
    gexf.write('      <attribute id="10" title="nb_organisms" type="long" />\n')
    if not light:
        for org, orgIndex in index.items():
            gexf.write(f'      <attribute id="{orgIndex + 12}" title="{org.name}" type="string" />\n')

    gexf.write('    </attributes>\n')
    gexf.write('    <attributes class="edge" mode="static">\n')
    gexf.write('      <attribute id="11" title="nb_genes" type="long" />\n')
    if not light:
        for org, orgIndex in index.items():
            gexf.write(f'      <attribute id="{orgIndex + len(index) + 12}" title="{org.name}" type="long" />\n')
    # gexf.write('      <attribute id="12" title="nb_organisms" type="long" />\n')#useless because it's the weight of the edge
    gexf.write('    </attributes>\n')
    gexf.write('    <meta>\n')
    gexf.write(f'      <creator>PPanGGOLiN {pkg_resources.get_distribution("ppanggolin").version}</creator>\n')
    gexf.write('    </meta>\n')

def writeGEXFnodes(gexf, light, soft_core = 0.95):
    gexf.write('    <nodes>\n')
    colors = {"persistent":'a="0" b="7" g="165" r="247"','shell':'a="0" b="96" g="216" r="0"', 'cloud':'a="0" b="255" g="222" r="121"'}
    if not light:
        index = pan.getIndex()

    for fam in pan.geneFamilies:
        name = Counter()
        product = Counter()
        gtype = Counter()
        l = []
        for gene in fam.genes:
            name[gene.name] +=1
            product[gene.product] += 1
            gtype[gene.type] += 1
            l.append(gene.stop - gene.start)

        gexf.write(f'      <node id="{fam.ID}" label="{fam.name}">\n')
        gexf.write(f'        <viz:color {colors[fam.namedPartition]} />\n')
        gexf.write(f'        <viz:size value="{len(fam.organisms)}" />\n')
        gexf.write(f'        <attvalues>\n')
        gexf.write(f'          <attvalue for="0" value="{len(fam.genes)}" />\n')
        gexf.write(f'          <attvalue for="1" value="{name.most_common(1)[0][0]}" />\n')
        gexf.write(f'          <attvalue for="2" value="{product.most_common(1)[0][0]}" />\n')
        gexf.write(f'          <attvalue for="3" value="{gtype.most_common(1)[0][0]}" />\n')
        gexf.write(f'          <attvalue for="4" value="{fam.namedPartition}" />\n')
        gexf.write(f'          <attvalue for="5" value="{fam.partition}" />\n')
        gexf.write(f'          <attvalue for="6" value="{"exact_accessory" if len(fam.organisms) != len(pan.organisms) else "exact_core"}" />\n')
        gexf.write(f'          <attvalue for="7" value="{"soft_core" if len(fam.organisms) > (len(pan.organisms)*soft_core) else "soft_accessory"}" />\n')
        gexf.write(f'          <attvalue for="8" value="{round(sum(l) / len(l),2)}" />\n')
        gexf.write(f'          <attvalue for="9" value="{ int(median(l))}" />\n')
        gexf.write(f'          <attvalue for="10" value="{len(fam.organisms)}" />\n')
        if not light:
            for org, genes in fam.getOrgDict().items():
                gexf.write(f'          <attvalue for="{index[org]+12}" value="{"|".join([ gene.ID for gene in genes])}" />\n')
        gexf.write(f'        </attvalues>\n')
        gexf.write(f'      </node>\n')
    gexf.write('    </nodes>\n')

def writeGEXFedges(gexf, light):
    gexf.write('    <edges>\n')
    edgeids = 0
    index = pan.getIndex()

    for edge in pan.edges:
        gexf.write(f'      <edge id="{edgeids}" source="{edge.source.ID}" target="{edge.target.ID}" weight="{len(edge.organisms)}">\n')
        gexf.write(f'        <viz:thickness value="{len(edge.organisms)}" />\n')
        gexf.write('        <attvalues>\n')
        gexf.write(f'          <attribute id="11" value="{len(edge.genePairs)}" />\n')
        if not light:
            for org, genes in edge.getOrgDict().items():
                gexf.write(f'          <attvalue for="{index[org]+len(index)+12}" value="{len(genes)}" />\n')
        gexf.write('        </attvalues>\n')
        gexf.write('      </edge>\n')
        edgeids+=1
    gexf.write('    </edges>\n')

def writeGEXFend(gexf):
    gexf.write("  </graph>")
    gexf.write("</gexf>")

def writeGEXF(output, light = True, soft_core = 0.95, compress=False):
    txt = "Writing the gexf file for the pangenome graph..."
    if light:
        txt = "Writing the light gexf file for the pangenome graph..."
    logging.getLogger().info(txt)
    outname = output + "/pangenomeGraph"
    outname += "_light" if light else ""
    outname += ".gexf"
    with write_compressed_or_not(outname,compress) as gexf:
        writeGEXFheader(gexf, light)
        writeGEXFnodes(gexf, light)
        writeGEXFedges(gexf, light)
        writeGEXFend(gexf)
    logging.getLogger().info(f"Done writing the gexf file : '{outname}'")

def writeMatrix(sep, ext, output, compress=False, geneNames = False):
    logging.getLogger().info(f"Writing the .{ext} file ...")
    outname = output + "/matrix." + ext
    with write_compressed_or_not(outname,compress) as matrix:

        index_org = {}
        default_dat = []
        for index, org in enumerate(pan.organisms):
            default_dat.append('0')
            index_org[org] = index

        matrix.write(sep.join(['"Gene"',#1
                                '"Non-unique Gene name"',#2
                                '"Annotation"',#3
                                '"No. isolates"',#4
                                '"No. sequences"',#5
                                '"Avg sequences per isolate"',#6
                                '"Accessory Fragment"',#7
                                '"Genome Fragment"',#8
                                '"Order within Fragment"',#9
                                '"Accessory Order with Fragment"',#10
                                '"QC"',#11
                                '"Min group size nuc"',#12
                                '"Max group size nuc"',#13
                                '"Avg group size nuc"']#14
                                +['"'+str(org)+'"' for org in pan.organisms])+"\n")#15
        default_genes = ['""'] * len(pan.organisms) if geneNames else ["0"] * len(pan.organisms)
        org_index = pan.getIndex()#should just return things
        for fam in pan.geneFamilies:
            genes = default_genes.copy()
            l = []
            product = Counter()
            for org, gene_list in fam.getOrgDict().items():
                genes[org_index[org]] = " ".join([ '"' + str(gene) + '"' for gene in gene_list]) if geneNames else str(len(gene_list))
                for gene in gene_list:
                    l.append(gene.stop - gene.start)
                    product[gene.product] +=1

            l = [ gene.stop - gene.start for gene in fam.genes ]
            matrix.write(sep.join(['"'+fam.name+'"',#1
                                    '"'+fam.namedPartition+'"',#2
                                    '"'+ str(product.most_common(1)[0][0])  +'"',#3
                                    '"' + str(len(fam.organisms)) + '"',#4
                                    '"' + str(len(fam.genes)) + '"',#5
                                    '"' + str(round(len(fam.genes)/len(fam.organisms),2)) + '"',#6
                                    '"NA"',#7
                                    '"NA"',#8
                                    '""',#9
                                    '""',#10
                                    '""',#11
                                    '"' + str(min(l)) + '"',#12
                                    '"' + str(max(l)) + '"',#13
                                    '"' + str(round(sum(l)/len(l),2)) + '"']#14
                                    +genes)+"\n")#15
    logging.getLogger().info(f"Done writing the matrix : '{outname}'")

def writeGenePresenceAbsence(output, compress=False):
    logging.getLogger().info(f"Writing the gene presence absence file ...")
    outname = output + "/gene_presence_absence.Rtab"
    with write_compressed_or_not(outname,compress) as matrix:
        index_org = {}
        default_dat = []
        for index, org in enumerate(pan.organisms):
            default_dat.append('0')
            index_org[org] = index

        matrix.write('\t'.join(['Gene']#14
                                +[str(org) for org in pan.organisms])+"\n")#15
        default_genes =  ["0"] * len(pan.organisms)
        org_index = pan.getIndex()#should just return things
        for fam in pan.geneFamilies:
            genes = default_genes.copy()
            for org in fam.organisms:
                genes[org_index[org]] = "1"

            matrix.write('\t'.join([fam.name]#14
                                    +genes)+"\n")#15
    logging.getLogger().info(f"Done writing the gene presence absence file : '{outname}'")

def writeStats(output, soft_core, dup_margin, compress=False):
    logging.getLogger().info("Writing pangenome statistics...")
    logging.getLogger().info("Writing statistics on persistent duplication...")
    single_copy_markers = set()#could use bitarrays if speed is needed
    with write_compressed_or_not(output + "/mean_persistent_duplication.tsv", compress) as outfile:
        outfile.write(f"#duplication_margin={round(dup_margin,3)}\n")
        outfile.write("\t".join(["persistent_family","duplication_ratio","mean_presence","is_single_copy_marker"]) + "\n")
        for fam in pan.geneFamilies:
            if fam.namedPartition == "persistent":
                mean_pres = len(fam.genes) / len(fam.organisms)
                nb_multi = 0
                for gene_list in fam.getOrgDict().values():
                    if len(gene_list) > 1:
                        nb_multi +=1
                dup_ratio = nb_multi / len(fam.organisms)
                is_SCM = False
                if dup_ratio < dup_margin:
                    is_SCM = True
                    single_copy_markers.add(fam)
                outfile.write("\t".join([fam.name,
                                         str(round(dup_ratio,3)),
                                         str(round(mean_pres,3)),
                                         str(is_SCM)]) + "\n")
    logging.getLogger().info("Done writing stats on persistent duplication")
    logging.getLogger().info("Writing genome per genome statistics (completeness and counts)...")
    soft = set()#could use bitarrays if speed is needed
    core = set()
    for fam in pan.geneFamilies:
        if len(fam.organisms) >= pan.number_of_organisms() * soft_core:
            soft.add(fam)
        if len(fam.organisms) == pan.number_of_organisms():
            core.add(fam)

    with write_compressed_or_not(output + "/organisms_statistics.tsv", compress) as outfile:
        outfile.write(f"#soft_core={round(soft_core,3)}\n")
        outfile.write(f"#duplication_margin={round(dup_margin,3)}\n")
        outfile.write("\t".join(["organism","nb_families","nb_persistent_families","nb_shell_families","nb_cloud_families","nb_exact_core","nb_soft_core","nb_genes","nb_persistent_genes","nb_shell_genes","nb_cloud_genes","nb_exact_core_genes","nb_soft_core_genes","completeness","nb_single_copy_markers"]) + "\n")

        for org in pan.organisms:
            fams = org.families
            nb_pers = 0
            nb_shell = 0
            nb_cloud = 0
            for fam in fams:
                if fam.namedPartition == "persistent":
                    nb_pers+=1
                elif fam.namedPartition == "shell":
                    nb_shell+=1
                else:
                    nb_cloud+=1

            nb_gene_pers = 0
            nb_gene_shell = 0
            nb_gene_soft = 0
            nb_gene_cloud = 0
            nb_gene_core = 0
            for gene in org.genes:
                if gene.family.namedPartition == "persistent":
                    nb_gene_pers +=1
                elif gene.family.namedPartition == "shell":
                    nb_gene_shell +=1
                else:
                    nb_gene_cloud += 1
                if gene.family in soft:
                    nb_gene_soft+=1
                    if gene.family in core:
                        nb_gene_core+=1
            completeness = "NA"
            if len(single_copy_markers) > 0:
                completeness = round((len(fams & single_copy_markers) / len(single_copy_markers))*100,2)
            outfile.write("\t".join(map(str,[org.name,
                                    len(fams),
                                    nb_pers,
                                    nb_shell,
                                    nb_cloud,
                                    len(core & fams),
                                    len(soft & fams),
                                    org.number_of_genes(),
                                    nb_gene_pers,
                                    nb_gene_shell,
                                    nb_gene_cloud,
                                    nb_gene_core,
                                    nb_gene_soft,
                                    completeness,
                                    len(fams & single_copy_markers)])) + "\n")

    logging.getLogger().info("Done writing genome per genome statistics")

def writeOrgFile(org, output, compress=False):
    with write_compressed_or_not(output + "/" + org.name + ".tsv",compress) as outfile:
        outfile.write("\t".join(["gene","contig","start","stop","strand","ori","family","nb_copy_in_org","partition","persistent_neighbors","shell_neighbors","cloud_neighbors"]) + "\n")
        for contig in org.contigs:
            for gene in contig.genes:
                nb_pers = 0
                nb_shell = 0
                nb_cloud = 0
                for neighbor in gene.family.neighbors:
                    if neighbor.namedPartition == "persistent":
                        nb_pers+=1
                    elif neighbor.namedPartition == "shell":
                        nb_shell+=1
                    else:
                        nb_cloud+=1
                outfile.write("\t".join(map(str,[gene.ID,
                                        contig.name,
                                        gene.start,
                                        gene.stop,
                                        gene.strand,
                                        "T" if (gene.name.upper() == "DNAA" or gene.product.upper() == "DNAA") else "F",
                                        gene.family.name,
                                        len(gene.family.getGenesPerOrg(org)),
                                        gene.family.namedPartition,
                                        nb_pers,
                                        nb_shell,
                                        nb_cloud
                                        ])) + "\n")

def writeProjections(output, compress=False):
    logging.getLogger().info("Writing the projection files...")
    outdir = output+"/projection"
    if not os.path.exists(outdir):
        os.makedirs(outdir)
    for org in pan.organisms:
        writeOrgFile(org, outdir, compress)
    logging.getLogger().info("Done writing the projection files")

def writeParts(output, soft_core, compress=False):
    logging.getLogger().info("Writing the list of gene families for each partitions...")
    if not os.path.exists(output + "/partitions"):
        os.makedirs(output + "/partitions")
    partSets = defaultdict(set)
    #initializing key, value pairs so that files exist even if they are empty
    for neededKey in ["undefined","soft_core","exact_core","exact_accessory","soft_accessory","persistent","shell","cloud"]:
        partSets[neededKey] = set()
    for fam in pan.geneFamilies:
        partSets[fam.namedPartition].add(fam.name)
        if fam.partition.startswith("S"):
            partSets[fam.partition].add(fam.name)
        if len(fam.organisms) >= len(pan.organisms) * soft_core:
            partSets["soft_core"].add(fam.name)
            if len(fam.organisms) == len(pan.organisms):
                partSets["exact_core"].add(fam.name)
            else:
                partSets["exact_accessory"].add(fam.name)
        else:
            partSets["soft_accessory"].add(fam.name)
            partSets["exact_accessory"].add(fam.name)

    for key, val in partSets.items():
        currKeyFile = open(output + "/partitions/" + key + ".txt","w")
        if len(val) > 0:
            currKeyFile.write('\n'.join(val) + "\n")
        currKeyFile.close()
    logging.getLogger().info("Done writing the list of gene families for each partition")

def writeGeneFamiliesTSV(output, compress=False):
    logging.getLogger().info("Writing the file providing the association between genes and gene families...")
    outname = output + "/gene_families.tsv"
    with write_compressed_or_not(outname,compress) as tsv:
        for fam in pan.geneFamilies:
            for gene in fam.genes:
            	tsv.write("\t".join([fam.name,gene.ID])+"\n")
    logging.getLogger().info(f"Done writing the file providing the association between genes and gene families : '{outname}'")
def writeFastaGenFam(output, compress=False):
    logging.getLogger().info("Writing the representative nucleic sequences of all the gene families...")
    outname = output + "/representative_gene_families.fna"
    with write_compressed_or_not(outname,compress) as fasta:
        getGeneSequencesFromFile(pan,fasta,[fam.name for fam in pan.geneFamilies])
    logging.getLogger().info(f"Done writing the representative nucleic sequences of all the gene families : '{outname}'")
def writeFastaProtFam(output, compress=False):
    logging.getLogger().info("Writing the representative proteic sequences of all the gene families...")
    outname = output + "/representative_gene_families.faa"
    with write_compressed_or_not(outname,compress) as fasta:
        bar = tqdm(range(pan.number_of_geneFamilies()),unit="prot families")
        for fam in list(pan.geneFamilies):
            fasta.write('>' +fam.name + "\n")
            fasta.write(fam.sequence + "\n")
            bar.update()
        bar.close()
    logging.getLogger().info(f"Done writing the representative proteic sequences of all the gene families : '{outname}'")

def writeGeneSequences(output, compress=False):
    logging.getLogger().info("Writing all the gene nucleic sequences...")
    outname = output + "/all_genes.fna"
    with write_compressed_or_not(outname,compress) as fasta:
        getGeneSequencesFromFile(pan,fasta)
    logging.getLogger().info(f"Done writing all the gene sequences : '{outname}'")

def writeFlatFiles(pangenome, output, cpu = 1, soft_core = 0.95, dup_margin = 0.05, csv=False, genePA = False, gexf = False, light_gexf = False, projection = False, stats = False, json = False, partitions=False, families_tsv = False, all_genes = False, all_prot_families = False, all_gene_families = False, compress = False):
    global pan
    pan = pangenome
    processes = []
    if any(x for x in [csv, genePA, gexf, light_gexf, projection, stats, json, partitions, families_tsv, all_genes, all_prot_families, all_gene_families]):
        #then it's useful to load the pangenome.
        checkPangenomeInfo(pan, needAnnotations=True, needFamilies=True, needGraph=True)
        ex_partitionned = Exception("The provided pangenome has not been partitionned. This is not compatible with any of the following options : --light_gexf, --gexf, --csv, --partitions")
        ex_genesClustered =  Exception("The provided pangenome has not gene families. This is not compatible with any of the following options : --families_tsv --all_prot_families --all_gene_families")
        ex_genomesAnnotated =  Exception("The provided pangenome has no annotated sequences. This is not compatible with any of the following options : --all_genes")
        ex_geneSequences =  Exception("The provided pangenome has no gene sequences. This is not compatible with any of the following options : --all_genes, --all_gene_families")
        ex_geneFamilySequences = Exception("The provided pangenome has no gene families. This is not compatible with any of the following options : --all_prot_families, all_gene_families")
        if not pan.status["partitionned"] in ["Loaded","Computed"] and (light_gexf or gexf or csv or projection or partitions):#could allow to write the csv or genePA without partition...
            raise ex_partitionned
        if not pan.status["genesClustered"] in ["Loaded","Computed"] and (families_tsv):
            raise ex_genesClustered
        if not pan.status["genomesAnnotated"] in ["Loaded","Computed"] and (all_genes):
            raise ex_genomesAnnotated
        if not pan.status["geneSequences"] in ["inFile"] and (all_genes or all_gene_families):
            raise ex_geneSequences
        if not pan.status["geneFamilySequences"] in ["Loaded","Computed"] and (all_prot_families):
            raise ex_geneFamilySequences
        pan.getIndex()#make the index because it will be used most likely
        with Pool(processes = cpu) as p:
            if csv:
                processes.append(p.apply_async(func = writeMatrix, args = (',', "csv", output, compress, True)))
            if genePA:
                processes.append(p.apply_async(func = writeGenePresenceAbsence, args = (output, compress)))
            if gexf:
                processes.append(p.apply_async(func = writeGEXF, args = (output, False, soft_core, compress)))
            if light_gexf:
                processes.append(p.apply_async(func = writeGEXF, args = (output, True, soft_core, compress)))
            if projection:
                processes.append(p.apply_async(func=writeProjections, args=(output, compress)))
            if stats:
                processes.append(p.apply_async(func=writeStats, args=(output, soft_core, dup_margin, compress)))
            if json:
                processes.append(p.apply_async(func=writeJSON, args=(output, compress)))
            if partitions:
                processes.append(p.apply_async(func=writeParts, args=(output, soft_core, compress)))
            if families_tsv:
                processes.append(p.apply_async(func=writeGeneFamiliesTSV, args=(output, compress)))
            if all_genes:
                processes.append(p.apply_async(func=writeGeneSequences, args=(output, compress)))
            if all_prot_families:
                processes.append(p.apply_async(func=writeFastaProtFam, args=(output, compress)))
            if all_gene_families:
                processes.append(p.apply_async(func=writeFastaGenFam, args=(output, compress)))
            for process in processes:
                process.get()#get all the results

def launch(args):
    mkOutdir(args.output, args.force)
    pangenome = Pangenome()
    pangenome.addFile(args.pangenome)
    writeFlatFiles(pangenome, args.output, args.cpu, args.soft_core, args.dup_margin, args.csv, args.Rtab, args.gexf, args.light_gexf, args.projection, args.stats, args.json, args.partitions, args.families_tsv, args.all_genes, args.all_prot_families, args.all_gene_families, args.compress)

def writeFlatSubparser(subparser):
    parser = subparser.add_parser("write", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    required = parser.add_argument_group(title = "Required arguments", description = "One of the following arguments is required :")
    required.add_argument('-p','--pangenome',  required=True, type=str, help="The pangenome .h5 file")
    required.add_argument('-o','--output', required=True, type=str, help="Output directory where the file(s) will be written")
    optional = parser.add_argument_group(title = "Optional arguments")
    optional.add_argument("--soft_core",required=False, default = 0.95, help = "Soft core threshold to use")
    optional.add_argument("--dup_margin", required=False, default=0.05, help = "minimum ratio of organisms in which the family must have multiple genes for it to be considered 'duplicated'")
    optional.add_argument("--gexf",required = False, action = "store_true", help = "write a gexf file with all the annotations and all the genes of each gene family")
    optional.add_argument("--light_gexf",required = False, action="store_true",help = "write a gexf file with the gene families and basic informations about them")
    optional.add_argument("--csv", required=False, action = "store_true",help = "csv file format as used by Roary, among others. The alternative gene ID will be the partition, if there is one")
    optional.add_argument("--Rtab", required=False, action = "store_true",help = "tabular file for the gene binary presence absence matrix")
    optional.add_argument("--projection", required=False, action = "store_true",help = "a csv file for each organism providing informations on the projection of the graph on the organism")
    optional.add_argument("--stats",required=False, action = "store_true",help = "tsv files with some statistics for each organism and for each gene family")
    optional.add_argument("--partitions", required=False, action = "store_true", help = "list of families belonging to each partition, with one file per partitions and one family per line")
    optional.add_argument("--compress",required=False, action="store_true",help="Compress the files in .gz")
    optional.add_argument("--json", required=False, action = "store_true", help = "Writes the graph in a json file format")
    optional.add_argument("--families_tsv", required=False, action = "store_true", help = "Write a tsv file providing the association between genes and gene families")
    optional.add_argument("--all_genes", required=False, action = "store_true", help = "Write all nucleotic CDS sequences")
    optional.add_argument("--all_prot_families", required=False, action = "store_true", help = "Write Write representative proteic sequences of all the gene families")
    optional.add_argument("--all_gene_families", required=False, action = "store_true", help = "Write representative nucleic sequences of all the gene families")
    return parser
