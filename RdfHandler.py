import json
import re
import traceback
from urllib.parse import quote

import osmium
import shapely.speedups
from shapely.wkb import loads

if shapely.speedups.available:
    shapely.speedups.enable()

wkbfab = osmium.geom.WKBFactory()


# May contain letters, numbers anywhere, and -:_ symbols anywhere except first and last position
reSimpleLocalName = re.compile(r'^[0-9a-zA-Z_]([-:0-9a-zA-Z_]*[0-9a-zA-Z_])?$')
reWikidataKey = re.compile(r'(.:)?wikidata$')
reWikidataValue = re.compile(r'^Q[1-9][0-9]*$')
reWikipediaValue = re.compile(r'^([-a-z]+):(.+)$')
reRoleValue = reSimpleLocalName


def addLocation(point, statements):
    spoint = str(point.x) + ' ' + str(point.y)
    if point.has_z:
        spoint += ' ' + str(point.z)
    statements.append('osmm:loc "Point(' + spoint + ')"^^geo:wktLiteral')


def addError(statements, tag, fallbackMessage):
    try:
        e = traceback.format_exc()
        statements.append(tag + ' ' + json.dumps(e, ensure_ascii=False))
    except:
        statements.append(tag + ' ' + fallbackMessage)


def getLastOsmSequence():
    query = '''SELECT ?date ?version WHERE {
  <http://www.openstreetmap.org> schema:dateModified ?date .
  <http://www.openstreetmap.org> schema:version ?ver .
}'''


class RdfHandler(osmium.SimpleHandler):
    def __init__(self, options):
        osmium.SimpleHandler.__init__(self)
        self.options = options

        self.last_stats = ''
        self.added_nodes = 0
        self.added_rels = 0
        self.added_ways = 0
        self.skipped_nodes = 0
        self.skipped_rels = 0
        self.skipped_ways = 0
        self.deleted_nodes = 0
        self.deleted_rels = 0
        self.deleted_ways = 0

        self.types = {
            'n': 'osmnode:',
            'w': 'osmway:',
            'r': 'osmrel:',
        }

        self.prefixes = [
            'prefix wd: <http://www.wikidata.org/entity/>',
            'prefix geo: <http://www.opengis.net/ont/geosparql#>',
            'prefix schema: <http://schema.org/>',

            'prefix osmroot: <https://www.openstreetmap.org>',
            'prefix osmnode: <https://www.openstreetmap.org/node/>',
            'prefix osmway: <https://www.openstreetmap.org/way/>',
            'prefix osmrel: <https://www.openstreetmap.org/relation/>',
            'prefix osmt: <https://wiki.openstreetmap.org/wiki/Key:>',
            'prefix osmm: <https://www.openstreetmap.org/meta/>',

            # 'prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>',
            # 'prefix xsd: <http://www.w3.org/2001/XMLSchema#>',
            # 'prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>',
            # 'prefix owl: <http://www.w3.org/2002/07/owl#>',
            # 'prefix wikibase: <http://wikiba.se/ontology#>',
            # 'prefix wdata: <https://www.wikidata.org/wiki/Special:EntityData/>',
            # 'prefix wd: <http://www.wikidata.org/entity/>',
            # 'prefix wds: <http://www.wikidata.org/entity/statement/>',
            # 'prefix wdref: <http://www.wikidata.org/reference/>',
            # 'prefix wdv: <http://www.wikidata.org/value/>',
            # 'prefix wdt: <http://www.wikidata.org/prop/direct/>',
            # 'prefix p: <http://www.wikidata.org/prop/>',
            # 'prefix ps: <http://www.wikidata.org/prop/statement/>',
            # 'prefix psv: <http://www.wikidata.org/prop/statement/value/>',
            # 'prefix psn: <http://www.wikidata.org/prop/statement/value-normalized/>',
            # 'prefix pq: <http://www.wikidata.org/prop/qualifier/>',
            # 'prefix pqv: <http://www.wikidata.org/prop/qualifier/value/>',
            # 'prefix pqn: <http://www.wikidata.org/prop/qualifier/value-normalized/>',
            # 'prefix pr: <http://www.wikidata.org/prop/reference/>',
            # 'prefix prv: <http://www.wikidata.org/prop/reference/value/>',
            # 'prefix prn: <http://www.wikidata.org/prop/reference/value-normalized/>',
            # 'prefix wdno: <http://www.wikidata.org/prop/novalue/>',
            # 'prefix skos: <http://www.w3.org/2004/02/skos/core#>',
            # 'prefix schema: <http://schema.org/>',
            # 'prefix cc: <http://creativecommons.org/ns#>',
            # 'prefix geo: <http://www.opengis.net/ont/geosparql#>',
            # 'prefix prov: <http://www.w3.org/ns/prov#>',
        ]


    def finalizeObject(self, obj, statements, type):
        if not obj.deleted and statements:
            statements.append('osmm:type "' + type + '"')
            statements.append('osmm:version "' + str(obj.version) + '"^^<http://www.w3.org/2001/XMLSchema#integer>')


    def parseTags(self, obj):
        if not obj.tags or obj.deleted:
            return None

        statements = []

        for tag in obj.tags:
            key = tag.k
            val = None
            if key == 'created_by':
                continue

            if not reSimpleLocalName.match(key):
                # Record any unusual tag name in a "osmm:badkey" statement
                statements.append('osmm:badkey ' + json.dumps(key, ensure_ascii=False))
                continue

            if 'wikidata' in key:
                if reWikidataValue.match(tag.v):
                    val = 'wd:' + tag.v
            elif 'wikipedia' in key:
                match = reWikipediaValue.match(tag.v)
                if match:
                    # For some reason, sitelinks stored in Wikidata WDQS have spaces instead of '_'
                    # https://www.mediawiki.org/wiki/Wikibase/Indexing/RDF_Dump_Format#Sitelinks
                    val = '<https://' + match.group(1) + '.wikipedia.org/wiki/' + \
                          quote(match.group(2).replace(' ', '_'), safe=';@$!*(),/~:') + '>'

            if val is None:
                val = json.dumps(tag.v, ensure_ascii=False)
            statements.append('osmt:' + key + ' ' + val)

        return statements


    def node(self, obj):
        statements = self.parseTags(obj)
        if statements:
            try:
                wkb = wkbfab.create_point(obj)
                point = loads(wkb, hex=True)
                addLocation(point, statements)
            except:
                addError(statements, 'osmm:loc:error', "Unable to parse location data")
            self.added_nodes += 1
        elif obj.deleted:
            self.deleted_nodes += 1
        else:
            self.skipped_nodes += 1

        self.finalizeObject(obj, statements, 'n')


    def way(self, obj):
        statements = self.parseTags(obj)
        if statements:
            statements.append('osmm:isClosed "' + ('true' if obj.is_closed() else 'false') + '"^^xsd:boolean')
            if self.options.addWayLoc:
                try:
                    wkb = wkbfab.create_linestring(obj)
                    point = loads(wkb, hex=True).representative_point()
                    addLocation(point, statements)
                except:
                    addError(statements, 'osmm:loc:error', "Unable to parse location data")
            self.added_ways += 1
        elif obj.deleted:
            self.deleted_ways += 1
        else:
            self.skipped_ways += 1
        self.finalizeObject(obj, statements, 'w')


    def relation(self, obj):
        statements = self.parseTags(obj)
        if obj.members:
            statements = statements if statements else []
            for mbr in obj.members:
                # ref role type
                ref = self.types[mbr.type] + str(mbr.ref)
                role = 'osmm:has'
                if mbr.role != '':
                    if reRoleValue.match(mbr.role):
                        role += ':' + mbr.role
                    else:
                        role += ':_'  # for unknown roles, use "osmm:has:_"
                statements.append(role + ' ' + ref)
            self.added_rels += 1
        elif obj.deleted:
            self.deleted_rels += 1
        else:
            self.skipped_rels += 1

        self.finalizeObject(obj, statements, 'r')


    def __enter__(self):
        return self


    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


    def close(self):
        pass


    def formatStats(self):
        res = 'Added: {0}n {1}w {2}r;  Skipped: {3}n {4}w {5}r;  Deleted: {6}n {7}w {8}r'.format(
            self.added_nodes, self.added_ways, self.added_rels,
            self.skipped_nodes, self.skipped_ways, self.skipped_rels,
            self.deleted_nodes, self.deleted_ways, self.deleted_rels,
        )
        if self.last_stats == res:
            res = ''
        else:
            self.last_stats = res
        return res