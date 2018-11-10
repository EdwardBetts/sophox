#!/usr/bin/env bash
set -e

# osm2pgsql expects password in this env var
export PGPASSWORD="${POSTGRES_PASSWORD}"

# Create a state file for the planet download. The state file is generated for 1 week previous
# in order not to miss any data changes. Since the planet dump is weekly and we generate this
# file when we download the planet-latest.osm.pbf file, we should not miss any changes.
if [[ ! -f "${OSM_PGSQL_DATA}/state.txt" ]]; then

    echo '########### Initializing osm-to-pgsql state file ###########'

    cp "${OSM_PGSQL_CODE}/sync_config.txt" "${OSM_PGSQL_DATA}"

    curl -SL \
        "https://replicate-sequences.osm.mazdermind.de/?"`date -u -d@"$$(( \`date +%s\`-1*7*24*60*60))" +"%Y-%m-%d"`"T00:00:00Z" \
        -o "${OSM_PGSQL_DATA}/state.txt"
fi


# Wait for the Postgres container to start up and possibly initialize the new db
sleep 30

if [[ ! -f "${OSM_PGSQL_DATA}/${OSM_FILE}.imported" ]]; then

    echo '########### Performing initial Postgres import with osm-to-pgsql ###########'

    # osm2pgsql cache memory is per CPU, not total
    OSM_PGSQL_MEM_IMPORT=$(( ${OSM_PGSQL_MEM_IMPORT} / ${OSM_PGSQL_CPU_IMPORT} ))

    set -x
    osm2pgsql \
        --create \
        --slim \
        --host "${POSTGRES_HOST}" \
        --username "${POSTGRES_USER}" \
        --database "${POSTGRES_DB}" \
        --flat-nodes "${OSM_PGSQL_DATA}/nodes.cache" \
        --cache "${OSM_PGSQL_MEM_IMPORT}" \
        --number-processes "${OSM_PGSQL_CPU_IMPORT}" \
        --hstore \
        --style "${OSM_PGSQL_CODE}/wikidata.style" \
        --tag-transform-script "${OSM_PGSQL_CODE}/wikidata.lua" \
        "${OSM_FILE}"
    set +x

    touch "${OSM_PGSQL_DATA}/${OSM_FILE}.imported"

fi

echo "########### Running osm-to-pgsql updates every ${LOOP_SLEEP} seconds ###########"

# osm2pgsql cache memory is per CPU, not total
OSM_PGSQL_MEM_UPDATE=$(( ${OSM_PGSQL_MEM_UPDATE} / ${OSM_PGSQL_CPU_UPDATE} ))
FIRST_LOOP=true

while :; do

    # It is ok for the import to crash - it should be safe to restart
    set +e

    # First iteration - log the osmosis + osm2pgsql commands
    if [[ "${FIRST_LOOP}" == "true" ]]; then
        FIRST_LOOP=false
        set -x
    fi

    osmosis \
        --read-replication-interval "workingDirectory=${OSM_PGSQL_DATA}" \
        --simplify-change \
        --write-xml-change \
        - \
    | osm2pgsql \
        --append \
        --slim \
        --host "${POSTGRES_HOST}" \
        --username "${POSTGRES_USER}" \
        --database "${POSTGRES_DB}" \
        --flat-nodes "${OSM_PGSQL_DATA}/nodes.cache" \
        --cache "${OSM_PGSQL_MEM_UPDATE}" \
        --number-processes "${OSM_PGSQL_CPU_UPDATE}" \
        --hstore \
        --style "${OSM_PGSQL_CODE}/wikidata.style" \
        --tag-transform-script "${OSM_PGSQL_CODE}/wikidata.lua" \
        -r xml \
        -

    set +x

    # Set LOOP_SLEEP to 0 to run this only once, otherwise sleep that many seconds until retry
    [[ "${LOOP_SLEEP}" -eq 0 ]] && exit $?
    sleep "${LOOP_SLEEP}" || exit

done
