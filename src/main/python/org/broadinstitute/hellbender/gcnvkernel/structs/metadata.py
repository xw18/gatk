import numpy as np
from typing import List, Set, Dict
from .interval import Interval
from .. import types
import logging
from ..io import io_consts

_logger = logging.getLogger(__name__)


class IntervalListMetadata:
    """Represents metadata gathered from an interval list.

    Attributes:
        interval_list: the interval list itself
        num_intervals: number of intervals in the list
        contig_set: set of contigs spanned by the intervals
        ordered_contig_list: list of contigs in the order that appears in the interval list
        num_contigs: number of contigs
        t_j: a 1d array representing the number of intervals for each contig (in the same order
            as `contig_list`)
    """
    def __init__(self, interval_list: List[Interval]):
        _logger.info("Generating intervals metadata...")
        self.interval_list = interval_list
        self.num_intervals = len(interval_list)
        self.contig_set = self._get_contig_set_from_interval_list(interval_list)
        self.ordered_contig_list = list(dict.fromkeys([interval.contig for interval in interval_list]))
        self.num_contigs = len(self.ordered_contig_list)

        # map from contig to indices in the interval list
        self.contig_interval_indices: Dict[str, List[int]] = \
            {contig: [ti for ti in range(len(interval_list))
                      if interval_list[ti].contig == contig]
             for contig in self.contig_set}

        # number of intervals per contig
        self.t_j = np.asarray([len(self.contig_interval_indices[self.ordered_contig_list[j]])
                               for j in range(self.num_contigs)], dtype=types.big_uint)

    @staticmethod
    def _get_contig_set_from_interval_list(interval_list: List[Interval]) -> Set[str]:
        return {interval.contig for interval in interval_list}


class SampleCoverageMetadata:
    """Represents basic metadata collected from a sample's coverage profile."""

    def __init__(self,
                 sample_name: str,
                 n_j: np.ndarray,
                 contig_list: List[str]):
        assert n_j.ndim == 1
        assert n_j.size == len(contig_list)

        self.sample_name = sample_name
        self.contig_list = contig_list

        # total count per contig
        self.n_j = n_j.astype(types.med_uint)

        # total count
        self.n_total = np.sum(self.n_j)
        self._contig_map = {contig: j for j, contig in enumerate(contig_list)}

    def _assert_contig_exists(self, contig: str):
        assert contig in self._contig_map, \
            "Sample ({0}) does not have coverage metadata for contig ({1})".format(self.sample_name, contig)

    def get_contig_total_count(self, contig: str):
        self._assert_contig_exists(contig)
        return self.n_j[self._contig_map[contig]]

    def get_total_count(self):
        return self.n_total

    @staticmethod
    def generate_sample_coverage_metadata(sample_name,
                                          n_t: np.ndarray,
                                          interval_list_metadata: IntervalListMetadata):
        n_j = np.zeros((len(interval_list_metadata.ordered_contig_list),), dtype=types.big_uint)
        for j, contig in enumerate(interval_list_metadata.ordered_contig_list):
            n_j[j] = np.sum(n_t[interval_list_metadata.contig_interval_indices[contig]])
        return SampleCoverageMetadata(sample_name, n_j, interval_list_metadata.ordered_contig_list)


class SamplePloidyMetadata:
    """Represents germline contig ploidy metadata of a sample. This metadata is either read from a
    file or is generated by contig ploidy determination model."""

    def __init__(self,
                 sample_name: str,
                 ploidy_j: np.ndarray,
                 ploidy_genotyping_quality_j: np.ndarray,
                 contig_list: List[str],
                 check_germline_contig_ploidy_for_homo_sapiens: bool = True):
        assert ploidy_j.ndim == 1
        assert ploidy_j.size == len(contig_list)
        assert ploidy_genotyping_quality_j.ndim == 1
        assert ploidy_genotyping_quality_j.size == len(contig_list)

        self.sample_name = sample_name
        self.contig_list = contig_list
        self.ploidy_j = ploidy_j.astype(types.small_uint)
        self.ploidy_genotyping_quality_j = ploidy_genotyping_quality_j.astype(types.floatX)
        self._contig_map = {contig: j for j, contig in enumerate(contig_list)}

        if check_germline_contig_ploidy_for_homo_sapiens:
            self.check_germline_contig_ploidy_for_homo_sapiens()

    def _assert_contig_exists(self, contig: str):
        assert contig in self._contig_map, \
            "Sample ({0}) does not have ploidy metadata for contig ({1})".format(self.sample_name, contig)

    def get_contig_ploidy(self, contig: str) -> int:
        self._assert_contig_exists(contig)
        return self.ploidy_j[self._contig_map[contig]]

    def get_contig_ploidy_genotyping_quality(self, contig: str):
        self._assert_contig_exists(contig)
        return self.ploidy_genotyping_quality_j[self._contig_map[contig]]

    def check_germline_contig_ploidy_for_homo_sapiens(self):
        autosomal_contigs = {str(j) for j in range(1, 23)}
        allosomal_contigs = {'X', 'Y'}
        all_standard_contigs = autosomal_contigs.union(allosomal_contigs)
        homo_sapiens_autosomal_contig_ploidy = 2
        homo_sapiens_sex_genotypes = {
            'SEX_XX': {'X': 2, 'Y': 0},
            'SEX_XY': {'X': 1, 'Y': 1}
        }
        general_warning_msg = "The presence of unmasked PAR regions and regions of low mappability in the " \
                              "coverage metadata can result in unreliable ploidy designations. It is recommended " \
                              "that the user verifies this designation by orthogonal methods."

        def rectify_contig(_contig: str):
            _contig_upper = _contig.upper()
            if _contig_upper.find('CHR') == 0:
                return _contig_upper[3:]
            else:
                return _contig_upper

        for j, contig in enumerate(self.contig_list):
            if rectify_contig(contig) not in all_standard_contigs:
                _logger.warning("Sample {0} has an unrecognized contig ({1}). Germline contig ploidy determination "
                                "may not be reliable for decoy/non-standard contigs.".format(self.sample_name, contig))
            if rectify_contig(contig) in autosomal_contigs and self.ploidy_j[j] != homo_sapiens_autosomal_contig_ploidy:
                _logger.warning("Sample {0} has an anomalous ploidy ({1}) for contig {2}. ".format(
                                    self.sample_name, self.ploidy_j[j], contig) + general_warning_msg)

        rectified_contig_list = [rectify_contig(contig) for contig in self.contig_list]
        rectified_contig_index = {contig: j for j, contig in enumerate(rectified_contig_list)}

        has_all_allosomal_contigs = all([contig in rectified_contig_list for contig in allosomal_contigs])
        has_some_allosomal_contigs = any([contig in rectified_contig_list for contig in allosomal_contigs])

        if has_all_allosomal_contigs:
            sample_sex_genotype_table = {contig: self.ploidy_j[rectified_contig_index[contig]]
                                         for contig in allosomal_contigs}
            is_normal_karyotype = any([sex_genotype_type == sample_sex_genotype_table
                                       for sex_genotype_type in homo_sapiens_sex_genotypes.values()])
            if not is_normal_karyotype:
                _logger.warning("Sample {0} has an anomalous karyotype ({1}). ".format(
                                    self.sample_name, sample_sex_genotype_table) + general_warning_msg)
        elif has_some_allosomal_contigs:
            for contig in allosomal_contigs:
                if contig in rectified_contig_index.keys():
                    allosomal_ploidy = self.ploidy_j[rectified_contig_index[contig]]
                    allowed_standard_allosomal_ploidies = {
                        sex_genotype_table[contig] for sex_genotype_table in homo_sapiens_sex_genotypes.values()}
                    if allosomal_ploidy not in allowed_standard_allosomal_ploidies:
                        _logger.warning("Sample {0} has some, but not all, of expected allosomal contigs, and "
                                        "contig {1} has a non-standard ploidy. ".format(
                                            self.sample_name, contig) + general_warning_msg)


class SampleReadDepthMetadata:
    """Represents global read depth and average ploidy metadata for a sample."""

    def __init__(self,
                 sample_name: str,
                 global_read_depth: float,
                 average_ploidy: float):
        assert global_read_depth > 0
        assert average_ploidy > 0
        self.sample_name = sample_name
        self.global_read_depth = global_read_depth
        self.average_ploidy = average_ploidy

    def get_global_read_depth(self):
        return self.global_read_depth

    def get_average_ploidy(self):
        return self.average_ploidy

    @staticmethod
    def generate_sample_read_depth_metadata(sample_coverage_metadata: SampleCoverageMetadata,
                                            sample_ploidy_metadata: SamplePloidyMetadata,
                                            interval_list_metadata: IntervalListMetadata) -> 'SampleReadDepthMetadata':
        assert sample_coverage_metadata.sample_name == sample_ploidy_metadata.sample_name
        assert interval_list_metadata.ordered_contig_list == sample_ploidy_metadata.contig_list

        sample_name = sample_ploidy_metadata.sample_name
        n_total = sample_coverage_metadata.n_total
        t_j = interval_list_metadata.t_j
        ploidy_j = sample_ploidy_metadata.ploidy_j

        effective_total_copies = float(np.sum(t_j * ploidy_j))
        global_read_depth = float(n_total) / effective_total_copies
        average_ploidy = effective_total_copies / float(np.sum(t_j))

        return SampleReadDepthMetadata(sample_name, global_read_depth, average_ploidy)


class SampleMetadataCollection:
    """Represents a collection of different metadata for a cohort."""

    def __init__(self):
        self.sample_coverage_metadata_dict: Dict[str, SampleCoverageMetadata] = dict()
        self.sample_ploidy_metadata_dict: Dict[str, SamplePloidyMetadata] = dict()
        self.sample_read_depth_metadata_dict: Dict[str, SampleReadDepthMetadata] = dict()

    def add_sample_coverage_metadata(self, sample_coverage_metadata: SampleCoverageMetadata):
        sample_name = sample_coverage_metadata.sample_name
        if sample_name in self.sample_coverage_metadata_dict.keys():
            raise SampleAlreadyInCollectionException(
                'Sample "{0}" already has coverage metadata annotations'.format(sample_name))
        else:
            self.sample_coverage_metadata_dict[sample_name] = sample_coverage_metadata

    def add_sample_ploidy_metadata(self, sample_ploidy_metadata: SamplePloidyMetadata):
        sample_name = sample_ploidy_metadata.sample_name
        if sample_name in self.sample_ploidy_metadata_dict.keys():
            raise SampleAlreadyInCollectionException(
                'Sample "{0}" already has ploidy metadata annotations'.format(sample_name))
        else:
            self.sample_ploidy_metadata_dict[sample_name] = sample_ploidy_metadata

    def add_sample_read_depth_metadata(self, sample_read_depth_metadata: SampleReadDepthMetadata):
        sample_name = sample_read_depth_metadata.sample_name
        if sample_name in self.sample_read_depth_metadata_dict.keys():
            raise SampleAlreadyInCollectionException(
                'Sample "{0}" already has read depth metadata annotations'.format(sample_name))
        else:
            self.sample_read_depth_metadata_dict[sample_name] = sample_read_depth_metadata

    def all_samples_have_coverage_metadata(self, sample_names: List[str]):
        return all([sample_name in self.sample_coverage_metadata_dict.keys()
                    for sample_name in sample_names])

    def all_samples_have_ploidy_metadata(self, sample_names: List[str]):
        return all([sample_name in self.sample_ploidy_metadata_dict.keys()
                    for sample_name in sample_names])

    def all_samples_have_read_depth_metadata(self, sample_names: List[str]):
        return all([sample_name in self.sample_read_depth_metadata_dict.keys()
                    for sample_name in sample_names])

    def get_sample_coverage_metadata(self, sample_name: str) -> SampleCoverageMetadata:
        return self.sample_coverage_metadata_dict[sample_name]

    def get_sample_ploidy_metadata(self, sample_name: str) -> SamplePloidyMetadata:
        return self.sample_ploidy_metadata_dict[sample_name]

    def get_sample_read_depth_metadata(self, sample_name: str) -> SampleReadDepthMetadata:
        return self.sample_read_depth_metadata_dict[sample_name]

    def get_sample_read_depth_array(self, sample_names: List[str]) -> np.ndarray:
        return np.asarray([self.sample_read_depth_metadata_dict[sample_name].get_global_read_depth()
                           for sample_name in sample_names], dtype=types.floatX)

    def get_sample_contig_ploidy_array(self, contig: str, sample_names: List[str]) -> np.ndarray:
        return np.asarray([self.get_sample_ploidy_metadata(sample_name).get_contig_ploidy(contig)
                           for sample_name in sample_names], dtype=types.small_uint)


class SampleAlreadyInCollectionException(Exception):
    def __init__(self, msg):
        super().__init__(msg)
