"""
================
Analysis Library
================
:Author: Orion Cohen, Tingzheng Hou
:Year: 2021
:Copyright: GNU Public License v3

Analysis library defines a variety of classes that analyze different aspects of solvation.
These classes are all instantiated with the solvation_data (pandas.DataFrame) generated
from the Solution class.

While the classes in analysis_library can be used in isolation, they are meant to be used
as attributes of the Solution class. This makes instantiating them and calculating the
solvation data a non-issue.
"""
import collections
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from statsmodels.tsa.stattools import acovf
from scipy.optimize import curve_fit
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components


class Speciation:
    """
    Calculate the solvation shells of every solute.

    Speciation organizes the solvation data by the type of residue
    coordinated with the central solvent. It collects this information in a
    pandas.DataFrame indexed by the frame and solute number. Each column is
    one of the solvents in the res_name column of the solvation data. The
    column value is how many residue of that type are in the solvation shell.

    Speciation provides the speciation of each solute in the speciation
    attribute, it also calculates the percentage of each unique
    shell and makes it available in the speciation_percent attribute.

    Additionally, there are methods for finding solvation shells of
    interest and computing how common certain shell configurations are.

    Parameters
    ----------
    solvation_data : pandas.DataFrame
        The solvation data frame output by Solution.
    n_frames : int
        The number of frames in solvation_data.
    n_solutes : int
        The number of solutes in solvation_data.

    Attributes
    ----------
    speciation : pandas.DataFrame
        a dataframe containing the speciation of every li ion at
        every trajectory frame. Indexed by frame and solute numbers.
        Columns are the solvent molecules and values are the number
        of solvent in the shell.
    speciation_percent : pandas.DataFrame
        the percentage of shells of each type. Columns are the solvent
        molecules and and values are the number of solvent in the shell.
        The final column is the percentage of total shell of that
        particular composition.
    co_occurrence : pandas.DataFrame
        The actual co-occurrence of solvents divided by the expected co-occurrence.
        In other words, given one molecule of solvent i in the shell, what is the
        probability of finding a solvent j relative to choosing a solvent at random
        from the pool of all coordinated solvents. This matrix will
        likely not be symmetric.
    """

    def __init__(self, solvation_data, n_frames, n_solutes):
        self.solvation_data = solvation_data
        self.n_frames = n_frames
        self.n_solutes = n_solutes
        self.speciation_data, self.speciation_percent = self._compute_speciation()
        self.co_occurrence = self._solvent_co_occurrence()

    def _compute_speciation(self):
        counts = self.solvation_data.groupby(["frame", "solvated_atom", "res_name"]).count()["res_ix"]
        counts_re = counts.reset_index(["res_name"])
        speciation_data = counts_re.pivot(columns=["res_name"]).fillna(0).astype(int)
        res_names = speciation_data.columns.levels[1]
        speciation_data.columns = res_names
        sum_series = speciation_data.groupby(speciation_data.columns.to_list()).size()
        sum_sorted = sum_series.sort_values(ascending=False)
        speciation_percent = sum_sorted.reset_index().rename(columns={0: 'count'})
        speciation_percent['count'] = speciation_percent['count'] / (self.n_frames * self.n_solutes)
        return speciation_data, speciation_percent

    @classmethod
    def _average_speciation(cls, speciation_frames, solute_number, frame_number):
        averages = speciation_frames.sum(axis=1) / (solute_number * frame_number)
        return averages

    def shell_percent(self, shell_dict):
        """
        Calculate the percentage of shells matching shell_dict.

        This function computes the percent of solvation shells that exist with a particular
        composition. The composition is specified by the shell_dict. The percent
        will be of all shells that match that specification.

        Attributes
        ----------
        shell_dict : dict of {str: int}
            a specification for a shell composition. Keys are residue names (str)
            and values are the number of desired residues. e.g. if shell_dict =
            {'mol1': 4} then the function will return the percentage of shells
            that have 4 mol1. Note that this may include shells with 4 mol1 and
            any number of other solvents. To specify a shell with 4 mol1 and nothing
            else, enter a dict such as {'mol1': 4, 'mol2': 0, 'mol3': 0}.

        Returns
        -------
        float
            the percentage of shells

        Examples
        --------

         .. code-block:: python

            # first define Li, BN, and FEC AtomGroups
            >>> solution = Solution(Li, {'BN': BN, 'FEC': FEC, 'PF6': PF6})
            >>> solution.run()
            >>> solution.speciation.shell_percent({'BN': 4, 'PF6': 1})
            0.0898
        """
        query_list = [f"{name} == {str(count)}" for name, count in shell_dict.items()]
        query = " and ".join(query_list)
        query_counts = self.speciation_percent.query(query)
        return query_counts['count'].sum()

    def find_shells(self, shell_dict):
        """
        Find all solvation shells that match shell_dict.

        This returns the frame, solute index, and composition of all solutes
        that match the composition given in shell_dict.

        Attributes
        ----------
        shell_dict : dict of {str: int}
            a specification for a shell composition. Keys are residue names (str)
            and values are the number of desired residues. e.g. if shell_dict =
            {'mol1': 4} then the function will return all shells
            that have 4 mol1. Note that this may include shells with 4 mol1 and
            any number of other solvents. To specify a shell with 4 mol1 and nothing
            else, enter a dict such as {'mol1': 4, 'mol2': 0, 'mol3': 0}.

        Returns
        -------
        pandas.DataFrame
            the index and composition of all shells that match shell_dict
        """
        query_list = [f"{name} == {str(count)}" for name, count in shell_dict.items()]
        query = " and ".join(query_list)
        query_counts = self.speciation_data.query(query)
        return query_counts

    def _solvent_co_occurrence(self):
        # calculate the co-occurrence of solvent molecules.
        expected_solvents_list = []
        actual_solvents_list = []
        for solvent in self.speciation_data.columns.values:
            # calculate number of available coordinating solvent slots
            shells_w_solvent = self.speciation_data.query(f'{solvent} > 0')
            n_solvents = shells_w_solvent.sum()
            # calculate expected number of coordinating solvents
            n_coordination_slots = n_solvents.sum() - len(shells_w_solvent)
            coordination_percentage = self.speciation_data.sum() / self.speciation_data.sum().sum()
            expected_solvents = coordination_percentage * n_coordination_slots
            # calculate actual number of coordinating solvents
            actual_solvents = n_solvents.copy()
            actual_solvents[solvent] = actual_solvents[solvent] - len(shells_w_solvent)
            # name series and append to list
            expected_solvents.name = solvent
            actual_solvents.name = solvent
            expected_solvents_list.append(expected_solvents)
            actual_solvents_list.append(actual_solvents)
        # make DataFrames
        actual_df = pd.concat(actual_solvents_list, axis=1)
        expected_df = pd.concat(expected_solvents_list, axis=1)
        # calculate correlation matrix
        correlation = actual_df / expected_df
        return correlation

    def plot_co_occurrence(self):
        """
        Plot the co-occurrence matrix of the solution.

        Co-occurrence as a heatmap with numerical values in addition to colors.

        Returns
        -------
        fig : matplotlib.Figure
        ax : matplotlib.Axes

        """
        solvent_names = self.speciation_data.columns.values
        fig, ax = plt.subplots()
        im = ax.imshow(self.co_occurrence)
        # We want to show all ticks...
        ax.set_xticks(np.arange(len(solvent_names)))
        ax.set_yticks(np.arange(len(solvent_names)))
        # ... and label them with the respective list entries
        ax.set_xticklabels(solvent_names, fontsize=14)
        ax.set_yticklabels(solvent_names, fontsize=14)
        # Let the horizontal axes labeling appear on top.
        ax.tick_params(top=True, bottom=False,
                       labeltop=True, labelbottom=False, )
        # Rotate the tick labels and set their alignment.
        plt.setp(ax.get_xticklabels(), rotation=-30, ha="right",
                 rotation_mode="anchor")
        # Loop over data dimensions and create text annotations.
        for i in range(len(solvent_names)):
            for j in range(len(solvent_names)):
                ax.text(j, i, round(self.co_occurrence.iloc[i, j], 2),
                        horizontalalignment="center",
                        verticalalignment="center",
                        color="black",
                        fontsize=14,
                        )
        fig.tight_layout()
        return fig, ax


class Coordination:
    """
    Calculate the coordination number for each solvent.

    Coordination calculates the coordination number by averaging the number of
    coordinated solvents in all of the solvation shells. This is equivalent to
    the typical method of integrating the solute-solvent RDF up to the solvation
    radius cutoff. As a result, Coordination calculates **species-species** coordination
    numbers, not the total coordination number of the solute. So if the coordination
    number of mol1 is 3.2, there are on average 3.2 mol1 residues within the solvation
    distance of each solute.

    The coordination numbers are made available as an average over the whole
    simulation and by frame.

    Parameters
    ----------
    solvation_data : pandas.DataFrame
        The solvation data frame output by Solution.
    n_frames : int
        The number of frames in solvation_data.
    n_solutes : int
        The number of solutes in solvation_data.

    Attributes
    ----------
    cn_dict : dict of {str: float}
        a dictionary where keys are residue names (str) and values are the
        average coordination number of that residue (float).
    cn_by_frame : pd.DataFrame
        a dictionary tracking the average coordination number of each
        residue across frames.
    coordinating_atoms : pd.DataFrame
        percent of each atom_type participating in solvation, calculated
         for each solvent.

    Examples
    --------

     .. code-block:: python

        # first define Li, BN, and FEC AtomGroups
        >>> solution = Solution(Li, {'BN': BN, 'FEC': FEC, 'PF6': PF6})
        >>> solution.run()
        >>> solution.coordination.cn_dict
        {'BN': 4.328, 'FEC': 0.253, 'PF6': 0.128}

    """

    def __init__(self, solvation_data, n_frames, n_solutes, atom_group):
        self.solvation_data = solvation_data
        self.n_frames = n_frames
        self.n_solutes = n_solutes
        self.cn_dict, self.cn_by_frame = self._average_cn()
        self.atom_group = atom_group
        self.coordinating_atoms = self._calculate_coordinating_atoms()

    def _average_cn(self):
        counts = self.solvation_data.groupby(["frame", "solvated_atom", "res_name"]).count()["res_ix"]
        cn_series = counts.groupby(["res_name", "frame"]).sum() / (
                self.n_solutes * self.n_frames
        )
        cn_by_frame = cn_series.unstack()
        cn_dict = cn_series.groupby(["res_name"]).sum().to_dict()
        return cn_dict, cn_by_frame

    def _calculate_coordinating_atoms(self, tol=0.005):
        """
        Determine which atom types are actually coordinating
        return the types of those atoms
        """
        # lookup atom types
        atom_types = self.solvation_data.reset_index(['atom_ix'])
        atom_types['atom_type'] = self.atom_group[atom_types['atom_ix']].types
        # count atom types
        atoms_by_type = atom_types[['atom_type', 'res_name', 'atom_ix']]
        type_counts = atoms_by_type.groupby(['res_name', 'atom_type']).count()
        solvent_counts = type_counts.groupby(['res_name']).sum()['atom_ix']
        # calculate percent of each
        solvent_counts_list = [solvent_counts[solvent] for solvent in type_counts.index.get_level_values(0)]
        type_percents = type_counts['atom_ix'] / solvent_counts_list
        type_percents.name = 'percent'
        # change index type
        type_percents = (type_percents
                         .reset_index(level=1)
                         .astype({'atom_type': str})
                         .set_index('atom_type', append=True)
                         )
        return type_percents[type_percents.percent > tol]


class Pairing:
    """
    Calculate the percent of solutes that are coordinated with each solvent.

    The pairing percentage is the percent of solutes that are coordinated with
    ANY solvent with matching type. So if the pairing of mol1 is 0.5, then 50% of
    solutes are coordinated with at least 1 mol1.

    The pairing percentages are made available as an average over the whole
    simulation and by frame.

    Parameters
    ----------
    solvation_data : pandas.DataFrame
        The solvation data frame output by Solution.
    n_frames : int
        The number of frames in solvation_data.
    n_solutes : int
        The number of solutes in solvation_data.
    n_solvents : dict of {str: int}
        The number of each kind of solvent.

    Attributes
    ----------
    pairing_dict : dict of {str: float}
        a dictionary where keys are residue names (str) and values are the
        percentage of solutes that contain that residue (float).
    pairing_by_frame : pd.DataFrame
        a dictionary tracking the average percentage of each
        residue across frames.
    percent_free_solvents : dict of {str: float}
        a dictionary containing the percent of each solvent that is free. e.g.
        not coordinated to a solute.

    Examples
    --------

     .. code-block:: python

        # first define Li, BN, and FEC AtomGroups
        >>> solution = Solution(Li, {'BN': BN, 'FEC': FEC, 'PF6': PF6})
        >>> solution.run()
        >>> solution.pairing.pairing_dict
        {'BN': 1.0, 'FEC': 0.210, 'PF6': 0.120}
    """

    def __init__(self, solvation_data, n_frames, n_solutes, n_solvents):
        self.solvation_data = solvation_data
        self.n_frames = n_frames
        self.n_solutes = n_solutes
        self.solvent_counts = n_solvents
        self.pairing_dict, self.pairing_by_frame = self._percent_coordinated()
        self.percent_free_solvents = self._percent_free_solvent()
        self.diluent_composition, self.diluent_by_frame = self._diluent_composition()

    def _percent_coordinated(self):
        # calculate the percent of solute coordinated with each solvent
        counts = self.solvation_data.groupby(["frame", "solvated_atom", "res_name"]).count()["res_ix"]
        pairing_series = counts.astype(bool).groupby(["res_name", "frame"]).sum() / (
            self.n_solutes
        )  # average coordinated overall
        pairing_by_frame = pairing_series.unstack()
        pairing_normalized = pairing_series / self.n_frames
        pairing_dict = pairing_normalized.groupby(["res_name"]).sum().to_dict()
        return pairing_dict, pairing_by_frame

    def _percent_free_solvent(self):
        # calculate the percent of each solvent NOT coordinated with the solute
        counts = self.solvation_data.groupby(["frame", "res_ix", "res_name"]).count()['dist']
        totals = counts.groupby(['res_name']).count() / self.n_frames
        n_solvents = np.array([self.solvent_counts[name] for name in totals.index.values])
        free_solvents = np.ones(len(totals)) - totals / n_solvents
        return free_solvents.to_dict()

    def _diluent_composition(self):
        coordinated_solvents = self.solvation_data.groupby(["frame", "res_name"]).nunique()["res_ix"]
        solvent_counts = pd.Series(self.solvent_counts)
        total_solvents = solvent_counts.reindex(coordinated_solvents.index, level=1)
        diluent_solvents = total_solvents - coordinated_solvents
        diluent_by_frame = diluent_solvents / diluent_solvents.groupby(['frame']).sum()
        diluent_composition = diluent_by_frame.groupby(['res_name']).mean().to_dict()
        return diluent_composition, diluent_by_frame


class Residence:
    """
    A residence time calculator:

    1.  filter on each solvent id
        2.  generate adjacency matrix for each frame
        3.  for each solute ix
                for each solvent ix
                    a. select frame-by-frame adjacency
                    b. calculate auto-covariance function
                    c. add to storage datastructure
        4. average all auto-covariance functions
        5. fit exponential function to averaged auto-covariance function
        6. calculate residence times and add to dict
    7. save residence times and finish
    """

    def __init__(self, solvation_data):
        self.solvation_data = solvation_data
        self.residence_times, self.fit_parameters = self._calculate_residence_coordination()

    def _calculate_residence_coordination(self):
        frame_solute_index = np.unique(self.solvation_data.index.droplevel(2))
        residence_times = {}
        fit_parameters = {}
        for res_name, res_solvation_data in self.solvation_data.groupby(['res_name']):
            adjacency_mini = Residence.calculate_adjacency_dataframe(res_solvation_data)
            adjacency_df = adjacency_mini.reindex(frame_solute_index, fill_value=0)
            auto_covariance = Residence._calculate_auto_covariance(adjacency_df)
            res_time, params = Residence._calculate_residence_time(auto_covariance)
            residence_times[res_name], fit_parameters[res_name] = res_time, params
        return residence_times, fit_parameters

    @staticmethod
    def exp(x, a, b, c):
        """
        An exponential decay function

        Args:
            x: Independent variable.
            a: Initial quantity.
            b: Exponential decay constant.
            c: Constant.

        Returns:
            The acf
        """
        return a * np.exp(-b * x) + c

    @staticmethod
    def _calculate_residence_time(auto_covariance):
        # Exponential fit of solvent-Li ACF
        auto_covariance_norm = auto_covariance / auto_covariance[0]
        # TODO: add cutoff time?
        try:
            params, param_covariance = curve_fit(
                Residence.exp,
                np.arange(len(auto_covariance_norm)),
                auto_covariance_norm,
                p0=(1, 0.1, 0.01),
            )
            tau = 1 / params[1]  # p
        except RuntimeError:
            tau, params = np.nan, (np.nan, np.nan, np.nan)
        return tau, params

    @staticmethod
    def _calculate_auto_covariance(adjacency_matrix):
        auto_covariances = []
        for solute_ix, df in adjacency_matrix.groupby(['solvated_atom']):
            non_zero_cols = df.loc[:, (df != 0).any(axis=0)]
            auto_covariance_df = non_zero_cols.apply(
                acovf,
                axis=0,
                result_type='expand',
                demean=False,
                unbiased=True,
                fft=True
            )
            auto_covariances.append(auto_covariance_df.values)
        auto_covariance = np.mean(np.concatenate(auto_covariances, axis=1), axis=1)
        return auto_covariance

    @staticmethod
    def calculate_adjacency_dataframe(solvation_data):
        adjacency_group = solvation_data.groupby(['frame', 'solvated_atom', 'res_ix'])
        adjacency_df = adjacency_group['dist'].count().unstack(fill_value=0)
        return adjacency_df


class Clustering:
    """
    1.  filter out all solvents that we don't want to be involved in clustering
        and store intermediate solvation data df
    2.  matrix multiple to get Li adjacency matrix
    3.  subgraph search to identify clusters
    4.  extract ids of participating solvents in each cluster
    5.  index on intermediate df to extract resix of anions in each cluster,
        call .unique().sum() to extract number of anions in each cluster
    6. save results in convenient format.

    # TODO: add low temp solvation data to test clustering more effectively
    """

    def __init__(self, solvation_data, solute_res_ix, res_name_map):
        self.solvation_data = solvation_data
        self.solute_res_ix = solute_res_ix
        self.res_name_map = res_name_map
    
    @staticmethod
    def unwrap_adjacency_dataframe(df):
        connections = df.reset_index(level=0).drop(columns='frame')
        idx = connections.columns.append(connections.index)
        directed = connections.reindex(index=idx, columns=idx, fill_value=0)
        undirected = directed.values + directed.values.T
        adjacency_matrix = csr_matrix(undirected)
        return adjacency_matrix
            
    def generate_clusters(self, solvents, solute_res_ix, res_name_map):
        """
        solvents is a string or list of strings
        """
        solvents = [solvents] if isinstance(solvents, str) else solvents
        solvation_subset = self.solvation_data[np.isin(self.solvation_data.res_name, solvents)]
        # reindex solvated_atom to residue indexes
        reindexed_subset = solvation_subset.reset_index(level=1)
        reindexed_subset.solvated_atom = solute_res_ix[reindexed_subset.solvated_atom]
        dropped_reindexed = reindexed_subset.set_index(['solvated_atom'], append=True)
        reindexed_subset = dropped_reindexed.reorder_levels(['frame', 'solvated_atom', 'atom_ix'])
        # create adjacency matrix from reindexed df
        graph = Residence.calculate_adjacency_dataframe(reindexed_subset)
        cluster_arrays = []
        # loop through each time step / frame
        for frame, df in graph.groupby('frame'):
            # drop empty columns
            df = df.loc[:, (df != 0).any(axis=0)]
            # save map from local index to residue index
            solute_map = df.index.get_level_values(1).values
            solvent_map = df.columns.values
            ix_to_res_ix = np.concatenate([solvent_map, solute_map])
            adjacency_df = Clustering.unwrap_adjacency_dataframe(df)
            _, clusters = connected_components(
                csgraph=adjacency_df,
                directed=False,
                return_labels=True
            )
            cluster_array = np.vstack([
                np.full(len(clusters), frame),  # frame
                clusters,  # clusters
                res_name_map[ix_to_res_ix],  # res_names
                ix_to_res_ix,  # res index
            ]).T
            cluster_arrays.append(cluster_array)
            # TODO: reshape all the clusters into a dataframe?
        # create and return clusters dataframe
        all_clusters = np.concatenate(cluster_arrays)
        cluster_df = (
            pd.DataFrame(all_clusters, columns=['frame', 'cluster', 'res_name', 'res_ix'])
            .set_index(['frame', 'cluster'])
            .sort_values(['frame', 'cluster'])
        )
        return cluster_df
