from _operator import itemgetter
from threading import Thread, Lock

from index_construction.index_IO import SequentialIndexWriter

from printer import ParsePrinter
from porterstemmer import Stemmer


class AbstractParseManager(object):

    def __init__(self, stats, verbose):
        self._cleaner = Cleaner()
        self.printer = ParsePrinter(verbose)
        self.lock = Lock()
        self.stats = stats
        self._ended_threads = 0
        self.block_positions = dict()

    def parse(self, collection):
        """Start BlockParser workers on each block of the collection."""
        self.printer.print_block_parse_start_message(len(collection.blocks))

    def signal_job_done(self, block_path, positions):
        with self.lock:
            self.block_positions[block_path] = positions
            self._ended_threads += 1
            self.printer.print_block_parse_end_message(self._ended_threads)


class DefaultParseManager(AbstractParseManager):

    def parse(self, collection):
        AbstractParseManager.parse(self, collection)
        block_parsers = [DefaultBlockParser(self, block, collection.id_storer, self._cleaner, self.printer)
                         for block in collection.blocks]
        for parser in block_parsers:
            parser.start()
        for parser in block_parsers:
            parser.join()
        self.stats.signal_end_of_merge()


class AbstractBlockParser(Thread):

    def __init__(self, manager, block, id_storer, cleaner, printer):
        Thread.__init__(self)
        self.daemon = True
        self._cleaner = cleaner
        self.printer = printer
        self.block = block
        self.id_storer = id_storer
        self.manager = manager

    def _process_line(self, line):
        raise NotImplementedError

    def run(self):
        stemmer = Stemmer()
        block = self.block
        id_storer = self.id_storer
        tokens_amount = 0
        reversed_index = dict()
        for doc_id in block.documents:
            doc_frequency_dict = dict()
            with open(id_storer.doc_map[doc_id]) as my_file:
                for line in my_file:
                    for word in self._process_line(line):
                        stem_word = stemmer(word)
                        tokens_amount += 1
                        doc_frequency_dict[stem_word] = doc_frequency_dict.get(stem_word, 0) + 1
            doc_frequency_dict = {w: v for w, v in doc_frequency_dict.items() if not self._cleaner.is_common_word(w)}
            for word in doc_frequency_dict.keys():
                term_id = id_storer.get_term_id(word)
                occurrence_list = reversed_index.get(term_id, list())
                occurrence_list.append((doc_id, doc_frequency_dict[word]))
                reversed_index[term_id] = occurrence_list
        for posting_list in reversed_index.values():
            self.manager.stats.process_posting_list(posting_list)
        writer = SequentialIndexWriter("indexes/" + block.block_path, len(reversed_index))
        for term_index in sorted(reversed_index.items(), key=itemgetter(0)):
            writer.append(term_index)
        writer.close()
        self.manager.signal_job_done(self.block.block_path, writer.positions)


class DefaultBlockParser(AbstractBlockParser):

    def _process_line(self, line):
        return line.split()


class Cleaner(object):

    def __init__(self, common_words_file="common_words"):
        self._common_words = dict()
        with open(common_words_file) as my_file:
            for line in my_file:
                self._common_words[line[:-1]] = True

    def is_common_word(self, word):
        return word in self._common_words
