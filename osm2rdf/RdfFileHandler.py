import gzip, logging, os
from datetime import datetime
from multiprocessing import Process, Queue
from RdfHandler import RdfHandler
import osmutils

log = logging.getLogger('osm2rdf')


def writerThread(id, queue, options):
    while True:
        fileId, data, last_timestamp, statsStr = queue.get()
        if fileId is None:
            log.debug('Wrk #{0} complete'.format(id))
            return

        write_file(id, options, fileId, data, last_timestamp, statsStr)


def write_file(workerId, options, fileId, data, last_timestamp, statsStr):
    start = datetime.now()

    os.makedirs(options.output_dir, exist_ok=True)
    filename = os.path.join(options.output_dir, 'osm-{0:06}.ttl.gz'.format(fileId))
    output = gzip.open(filename, 'xt', compresslevel=3)

    output.write(options.file_header)
    for item in data:
        typ, id, statements = item
        text = typ + str(id) + '\n' + ';\n'.join(osmutils.toStrings(statements)) + '.\n\n'
        output.write(text)

    if last_timestamp.year > 2000:  # Not min-year
        output.write(
            '\nosmroot: schema:dateModified {0} .'.format(osmutils.format_date(last_timestamp)))

    output.flush()
    output.close()

    seconds = (datetime.now() - start).total_seconds()
    log.info('{0} done in {1} s by wrkr #{2}: {3}'.format(filename, seconds, workerId, statsStr))


class RdfFileHandler(RdfHandler):
    def __init__(self, options):
        super(RdfFileHandler, self).__init__(options)
        self.job_counter = 1
        self.length = None
        self.output = None
        self.maxStatementCount = self.options.maxStatementsPerFile * 1000
        self.pending = []
        self.pendingStatements = 0
        self.options.file_header = '\n'.join(['@' + p + ' .' for p in osmutils.prefixes]) + '\n\n'

        # Queue should contain at most 1 item, making the total number of batches in memory to be
        # number_of_workers + one_in_query + one_being_assembled_by_main_thread
        self.queue = Queue(1)

        self.writers = []
        for id in range(options.worker_count):
            process = Process(target=writerThread, args=(id, self.queue, self.options))
            self.writers.append(process)
            process.start()

    def finalize_object(self, obj, statements, obj_type):
        super(RdfFileHandler, self).finalize_object(obj, statements, obj_type)

        if statements:
            self.pending.append((osmutils.types[obj_type], obj.id, statements))
            self.pendingStatements += 2 + len(statements)

            if self.pendingStatements > self.maxStatementCount:
                self.flush()

    def flush(self):
        if self.pendingStatements == 0:
            return

        statsStr = self.format_stats()
        self.queue.put((self.job_counter, self.pending, self.last_timestamp, statsStr))

        self.job_counter += 1
        self.pending = []
        self.pendingStatements = 0

    def run(self, input_file):
        if self.options.addWayLoc:
            self.apply_file(input_file, locations=True, idx=self.get_index_string())
        else:
            self.apply_file(input_file)

        self.flush()

        # Send stop signal to each worker, and wait for all to stop
        for p in self.writers:
            self.queue.put((None, None, None, None))
        self.queue.close()
        for p in self.writers:
            p.join()
