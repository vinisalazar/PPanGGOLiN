#!/usr/bin/env python3
#coding:utf-8

#default libraries
import argparse
from multiprocessing import Pool
import logging
import os
import time

#installed libraries
from tqdm import tqdm

#local libraries
from ppanggolin.annotate import  annotate_organism, read_fasta, get_dna_sequence
from ppanggolin.pangenome import Pangenome
from ppanggolin.genome import Organism, Gene, RNA
from ppanggolin.utils import read_compressed_or_not, mkFilename, get_num_lines
from ppanggolin.formats import writePangenome

def detect_filetype(filename):
    """ detects whether the current file is gff3, gbk/gbff or unknown. If unknown, it will raise an error"""
    with read_compressed_or_not(filename) as f:
        firstLine = f.readline()
    if firstLine.startswith("LOCUS       "):#then this is probably a gbff/gbk file
        return "gbff"
    elif firstLine.startswith("##gff-version 3"):
        return 'gff'
    else:
        raise Exception("Filetype was not gff3 (file starts with '##gff-version 3') nor gbff/gbk (file starts with 'LOCUS       '). Only those two file formats are supported (for now).")

def create_gene(org, contig, ID, dbxref, start, stop, strand, gene_type, position = None, gene_name = "", product = "", genetic_code = 11):
    if any('MaGe' in dbref for dbref in dbxref):
        if gene_name == "":
            gene_name = ID
        for val in dbxref:
            if 'MaGe' in val:
                ID = val.split(':')[1]
                break
    if gene_type == "CDS":
        newGene = Gene(ID)
        newGene.fill_annotations(start = start,
                                stop = stop,
                                strand = strand,
                                geneType = gene_type,
                                position = position,
                                name = gene_name,
                                product = product,
                                genetic_code = genetic_code)
        contig.addGene(newGene)
    else:#either a CDS, or a RNA
        newGene = RNA(ID)
        newGene.fill_annotations(start = start,
                                stop = stop,
                                strand = strand,
                                geneType = gene_type,
                                name = gene_name,
                                product = product)
        contig.addRNA(newGene)
    newGene.fill_parents(org, contig)

def read_org_gbff(pangenome, organism, gbff_file_path, circular_contigs, getSeq, pseudo = False):
    """ reads a gbff file and fills Organism, Contig and Genes objects based on information contained in this file """
    org = Organism(organism)

    logging.getLogger().debug("Extracting genes informations from the given gbff")
    # revert the order of the file, to read the first line first.
    lines = read_compressed_or_not(gbff_file_path).readlines()[::-1]
    while len(lines) != 0:
        line = lines.pop()
        # beginning of contig
        if line.startswith('LOCUS'):
            is_circ = False
            if "CIRCULAR" in line.upper():#this line contains linear/circular word telling if the dna sequence is circularized or not
                is_circ = True
            while not line.startswith('FEATURES'):
                if line.startswith('VERSION'):
                    contigID = line[12:].strip()
                    if contigID in circular_contigs:
                        is_circ = True
                    contig = org.getOrAddContig(contigID, is_circ)
                line = lines.pop()
        # start of the feature object.
        dbxref = set()
        gene_name = ""
        product = ""
        locus_tag = ""
        objType = ""
        genetic_code = ""
        usefulInfo = False
        start = None
        end = None
        strand = None
        line = lines.pop()
        while not line.startswith("ORIGIN"):
            currType = line[5:21].strip()
            if currType != "":
                if usefulInfo:
                    #special bit to cope with MaGe's genbank format...
                    create_gene(org, contig, locus_tag, dbxref, start, end, strand, objType, len(contig.genes), gene_name, product, genetic_code)
                usefulInfo = False
                objType = currType
                if objType in ['CDS','rRNA','tRNA']:
                    dbxref = set()
                    gene_name = ""
                    try:
                        if not 'join' in line[21:]:
                            usefulInfo = True
                            if line[21:].startswith('complement('):
                                strand = "-"
                                start, end = line[32:].replace(
                                    ')', '').split("..")
                            else:
                                strand = "+"
                                start, end = line[21:].strip().split('..')
                            if '>' in start or '<' in start or '>' in end or '<' in end:
                                usefulInfo = False
                    except ValueError:
                        pass
                        #don't know what to do with that, ignoring for now.
                        #there is a protein with a frameshift mecanism.
            elif usefulInfo:# current info goes to current objtype, if it's useful.
                if line[21:].startswith("/db_xref"):
                    dbxref.add(line.split("=")[1].replace('"', '').strip())
                elif line[21:].startswith("/locus_tag"):
                    locus_tag = line.split("=")[1].replace('"', '').strip()
                elif line[21:].startswith('/gene'):#gene name
                    gene_name = line.split("=")[1].replace('"', '').strip()
                elif line[21:].startswith('/transl_table'):
                    genetic_code = line.split("=")[1].replace('"', '').strip()
                elif line[21:].startswith('/product'):#need to loop as it can be more than one line long
                    product = line.split('=')[1].replace('"', '').strip()
                    if line.count('"') == 1:#then the product line is on multiple lines
                        line = lines.pop()
                        product += line.strip().replace('"', '')
                        while line.count('"') != 1:
                            line = lines.pop()
                            product += line.strip().replace('"', '')
                #if it's a pseudogene, we're not keeping it.
                elif line[21:].startswith("/pseudo") and not pseudo:
                    usefulInfo = False
                #that's probably a 'stop' codon into selenocystein.
                elif line[21:].startswith("/transl_except"):
                    usefulInfo = False
            line = lines.pop()
            #end of contig
        if usefulInfo:#saving the last element...
            create_gene(org, contig, locus_tag, dbxref, start, end, strand, objType, len(contig.genes), gene_name, product, genetic_code)

        if getSeq:
            line = lines.pop()#first sequence line.
            #if the seq was to be gotten, it would be here.
            sequence = ""
            while not line.startswith('//'):
                sequence += line[10:].replace(" ", "").strip().upper()
                line = lines.pop()
            #get each gene's sequence.
            for gene in contig.genes:
                gene.add_dna(get_dna_sequence(sequence, gene))

    pangenome.addOrganism(org)

def read_org_gff(pangenome, organism, gff_file_path, circular_contigs, getSeq, pseudo = False):
    (GFF_seqname, _, GFF_type, GFF_start, GFF_end, _, GFF_strand, _, GFF_attribute) = range(0,9)#missing values : source, score, frame. They are unused.
    def getGffAttributes(gff_fields):
        """
            Parses the gff attribute's line and outputs the attributes in a dict structure.
            :param gff_fields: a gff line stored as a list. Each element of the list is a column of the gff.
            :type list:
            :return: attributes:
            :rtype: dict
        """
        attributes_field = [f for f in gff_fields[GFF_attribute].strip().split(';') if len(f)>0]
        attributes = {}
        for att in attributes_field:
            (key, value) = att.strip().split('=')
            attributes[key.upper()]=value
        return attributes

    def getIDAttribute(attributes):
        """
            Gets the ID of the element from which the provided attributes were extracted. Raises an error if no ID is found.
            :param attribute:
            :type dict:
            :return: ElementID:
            :rtype: string
        """
        ElementID = attributes.get("ID")
        if not ElementID:
            logging.getLogger().error("Each CDS type of the gff files must own a unique ID attribute. Not the case for file: "+gff_file_path)
            exit(1)
        return ElementID

    hasFasta = False
    fastaString = ""
    org = Organism(organism)
    with read_compressed_or_not(gff_file_path) as gff_file:
        for line in gff_file:
            if hasFasta:
                fastaString += line
                continue
            elif line.startswith('##',0,2):
                if line.startswith('FASTA',2,7):
                    if not getSeq:#if getting the sequences is useless...
                        break
                    hasFasta = True
                elif line.startswith('sequence-region',2,17):
                    fields = [el.strip() for el in line.split()]
                    contig = org.getOrAddContig(fields[1], True if fields[1] in circular_contigs else False)
                continue
            elif line.startswith('#!',0,2):## special refseq comment lines for versionning softs, assemblies and annotations.
                continue
            gff_fields = [el.strip() for el in line.split('\t')]
            attributes = getGffAttributes(gff_fields)
            pseudogene = False
            if gff_fields[GFF_type] == 'region':
                if gff_fields[GFF_seqname] in circular_contigs:
                    contig.is_circular = True
            elif gff_fields[GFF_type] == 'CDS' or "RNA" in gff_fields[GFF_type]:
                geneID = attributes.get("PROTEIN_ID")#if there is a 'PROTEIN_ID' attribute, it's where the ncbi stores the actual gene ids, so we use that.
                if geneID is None:#if its not found, we get the one under the 'ID' field which must exist (otherwise not a gff3 compliant file)
                    geneID = getIDAttribute(attributes)
                try:
                    name = attributes.pop('NAME')
                except KeyError:
                    try:
                        name = attributes.pop('GENE')
                    except KeyError:
                        name = ""
                if "pseudo" in attributes or "pseudogene" in attributes:
                    pseudogene = True
                try:
                    product = attributes.pop('PRODUCT')
                except KeyError:
                    product = ""

                try:
                    genetic_code = attributes.pop("TRANSL_TABLE")
                except KeyError:
                    genetic_code = "11"
                if contig.name != gff_fields[GFF_seqname]:
                    contig = org.getOrAddContig(gff_fields[GFF_seqname])#get the current contig
                if gff_fields[GFF_type] == "CDS" and (not pseudogene or (pseudogene and pseudo)):
                    gene = Gene(geneID)
                    #here contig is filled in order, so position is the number of genes already stored in the contig.
                    gene.fill_annotations(start = int(gff_fields[GFF_start]),
                                        stop = int(gff_fields[GFF_end]),
                                        strand =gff_fields[GFF_strand],
                                        geneType = gff_fields[GFF_type],
                                        position = len(contig.genes),
                                        name = name,
                                        product=product,
                                        genetic_code = genetic_code )
                    gene.fill_parents(org, contig)
                    contig.addGene(gene)
                elif "RNA" in gff_fields[GFF_type]:
                    rna = RNA(geneID)
                    rna.fill_annotations(start = int(gff_fields[GFF_start]),
                                        stop = int(gff_fields[GFF_end]),
                                        strand =gff_fields[GFF_strand],
                                        geneType = gff_fields[GFF_type],
                                        name = name,
                                        product=product)
                    rna.fill_parents(organism, contig)
                    contig.addRNA(rna)

    ### GET THE FASTA SEQUENCES OF THE GENES
    if hasFasta and fastaString != "":
        contigSequences = read_fasta( org, fastaString.split('\n'))
        for contig in org.contigs:
            for gene in contig.genes:
                gene.add_dna(get_dna_sequence(contigSequences[contig.name], gene))
            for rna in contig.RNAs:
                rna.add_dna(get_dna_sequence(contigSequences[contig.name], rna))
    elif not hasFasta:
        pangenome.status["geneSequences"] = "No"
    pangenome.addOrganism(org)

def readAnnotations(pangenome, organisms_file, getSeq = True, pseudo = False):
    logging.getLogger().info("Reading "+organisms_file+" the list of organism files ...")

    bar = tqdm(read_compressed_or_not(organisms_file),total=get_num_lines(organisms_file), unit = "annotation file")
    pangenome.status["geneSequences"] = "Computed"#we assume there are gene sequences in the annotation files, unless a gff file without fasta is met (which is the only case where sequences can be asbent)
    for line in bar:
        elements = [el.strip() for el in line.split("\t")]
        if len(elements)<=1:
            logging.getLogger().error("No tabulation separator found in organisms file")
            exit(1)
        bar.set_description("Processing "+elements[1].split("/")[-1])
        bar.refresh()
        filetype = detect_filetype(elements[1])
        if filetype == "gff":
            read_org_gff(pangenome, elements[0], elements[1], elements[2:], getSeq, pseudo)
        elif filetype == "gbff":
            read_org_gbff(pangenome, elements[0], elements[1], elements[2:], getSeq, pseudo)
    bar.close()
    pangenome.status["genomesAnnotated"] = "Computed"
    pangenome.parameters["annotation"] = {}
    pangenome.parameters["annotation"]["read_annotations_from_file"] = True

def getGeneSequencesFromFastas(pangenome, fasta_file):
    fastaDict = {}
    for line in read_compressed_or_not(fasta_file):
        elements = [el.strip() for el in line.split("\t")]
        if len(elements)<=1:
            logging.getLogger().error("No tabulation separator found in organisms file")
            exit(1)
        org = pangenome.addOrganism(elements[0])
        with read_compressed_or_not(elements[1]) as currFastaFile:
            fastaDict[org] = read_fasta(org, currFastaFile)
    if not set(pangenome.organisms) <= set(fastaDict.keys()):
        missing = len(pangenome.organisms) - len(set(pangenome.organisms) & set(fastaDict.keys()))
        raise Exception(f"Not all of your pangenome's organisms are present within the provided fasta file. {missing} are missing (out of {len(pangenome.organisms)}).")
    
    for org in pangenome.organisms:
        try:
            for contig in org.contigs:
                for gene in contig.genes:
                    gene.add_dna(get_dna_sequence(fastaDict[org][contig.name], gene))
                for rna in contig.RNAs:
                    rna.add_dna(get_dna_sequence(fastaDict[org][contig.name], gene))
        except KeyError:
            msg = f"Fasta file for organism {org.name} did not have the contig {contig.name} that was read from the annotation file. "
            msg += f"The provided contigs in the fasta were : { ', '.join([contig for contig in fastaDict[org].keys()])}."
            raise KeyError(msg)
    pangenome.status["geneSequences"] = "Computed"

def launchAnnotateOrganism(pack):
    return annotate_organism(*pack)

def annotatePangenome(pangenome, fastaList, tmpdir, cpu, translation_table="11", kingdom = "bacteria", norna=False,  overlap=True):
    logging.getLogger().info(f"Reading {fastaList} the list of organism files")

    arguments = []
    for line in read_compressed_or_not(fastaList):
        elements = [el.strip() for el in line.split("\t")]
        if len(elements)<=1:
            logging.getLogger().error("No tabulation separator found in organisms file")
            exit(1)
        arguments.append((elements[0], elements[1], elements[2:], translation_table, kingdom, norna, tmpdir, overlap))
    if len(arguments) == 0:
        raise Exception("There are no genomes in the provided file")
    logging.getLogger().info(f"Annotating {len(arguments)} genomes using {cpu} cpus...")
    with Pool(processes = cpu) as p:
        bar = tqdm(range(len(arguments)), unit = "genome")
        for organism in p.imap_unordered(launchAnnotateOrganism, arguments):
            bar.update()
            pangenome.addOrganism(organism)
        p.close()
        p.join()
    bar.close()

    logging.getLogger().info("Done annotating genomes")
    pangenome.status["genomesAnnotated"] = "Computed"#the pangenome is now annotated.
    pangenome.status["geneSequences"] = "Computed"#the gene objects have their respective gene sequences.
    pangenome.parameters["annotation"] = {}
    pangenome.parameters["annotation"]["remove_Overlapping_CDS"] = overlap
    pangenome.parameters["annotation"]["annotate_RNA"] = True if not norna else False
    pangenome.parameters["annotation"]["kingdom"] = kingdom
    pangenome.parameters["annotation"]["translation_table"] = translation_table
    pangenome.parameters["annotation"]["read_annotations_from_file"] = False

def launch(args):
    filename = mkFilename(args.basename, args.output, args.force)
    pangenome = Pangenome()
    if args.fasta is not None and args.anno is None:
        annotatePangenome(pangenome, args.fasta, args.tmpdir, args.cpu,  args.translation_table, args.kingdom, args.norna, args.overlap)
    elif args.anno is not None:
        readAnnotations(pangenome, args.anno, pseudo = args.use_pseudo)
        if pangenome.status["geneSequences"] == "No":
            if args.fasta:
                getGeneSequencesFromFastas(pangenome, args.fasta)
            else:
                logging.getLogger().warning("You provided gff files without sequences, and you did not provide fasta sequences. Thus it was not possible to get the gene sequences.")
                logging.getLogger().warning("You will be able to proceed with your analysis ONLY if you provide the clustering results in the next step.")

    writePangenome(pangenome, filename, args.force)

def syntaSubparser(subparser):
    parser = subparser.add_parser("annotate", formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    required = parser.add_argument_group(title = "Required arguments", description = "One of the following arguments is required :")
    required.add_argument('--fasta',  required=False, type=str, help="A tab-separated file listing the organism names, and the fasta filepath of its genomic sequence(s) (the fastas can be compressed with gzip). One line per organism.")
    required.add_argument('--anno', required=False, type=str, help="A tab-separated file listing the organism names, and the gff/gbff filepath of its annotations (the files can be compressed with gzip). One line per organism. If this is provided, those annotations will be used.")

    optional = parser.add_argument_group(title = "Optional arguments")
    optional.add_argument('-o','--output', required=False, type=str, default="ppanggolin_output"+time.strftime("_DATE%Y-%m-%d_HOUR%H.%M.%S", time.localtime())+"_PID"+str(os.getpid()), help="Output directory")
    optional.add_argument('--overlap', required=False, action='store_false',default=True, help="Use to not remove genes overlapping with RNA features.")
    optional.add_argument("--norna", required=False, action="store_true", default=False, help="Use to avoid annotating RNA features.")
    optional.add_argument("--kingdom",required = False, type = str.lower, default = "bacteria", choices = ["bacteria","archaea"], help = "Kingdom to which the prokaryota belongs to, to know which models to use for rRNA annotation.")
    optional.add_argument("--translation_table",required=False, default="11", help = "Translation table (genetic code) to use.")
    optional.add_argument("--basename",required = False, default = "pangenome", help = "basename for the output file")
    optional.add_argument("--use_pseudo",required=False, action="store_true",help = "In the context of provided annotation, use this option to use pseudogenes. (Default behavior is to ignore them)")
    return parser