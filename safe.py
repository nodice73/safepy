import configparser
import os
import sys
import textwrap
import time
import argparse
import pickle

# Necessary check to make sure code runs both in Jupyter and in command line
if 'matplotlib' not in sys.modules:
    import matplotlib
    matplotlib.use('Agg')

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import multiprocessing as mp

from matplotlib.colors import LinearSegmentedColormap
from matplotlib import ticker, cm
from tqdm import tqdm
from functools import partial
from scipy.stats import hypergeom
from itertools import compress
from scipy.cluster.hierarchy import linkage, dendrogram, fcluster

from .safe_io import *
from .safe_colormaps import *


class SAFE:

    def __init__(self,
                 path_to_ini_file='',
                 verbose=True):

        self.verbose = verbose

        self.path_to_safe_data = None
        self.path_to_network_file = None
        self.path_to_attribute_file = None

        self.graph = None
        self.node_key_attribute = 'key'

        self.attributes = None
        self.nodes = None
        self.node2attribute = None
        self.num_nodes_per_attribute = None
        self.attribute_sign = None

        self.node_distance_metric = None
        self.neighborhood_radius_type = None
        self.neighborhood_radius = None

        self.num_permutations = 1000
        self.enrichment_type = None
        self.enrichment_threshold = 0.05
        self.enrichment_max_log10 = 16
        self.attribute_enrichment_min_size = 10

        self.neighborhoods = None

        self.pvalues_neg = None
        self.pvalues_pos = None
        self.nes = None
        self.nes_threshold = None
        self.nes_binary = None

        self.attribute_unimodality_metric = 'connectivity'
        self.attribute_distance_metric = 'jaccard'
        self.attribute_distance_threshold = 0.75

        self.domains = None
        self.node2domain = None

        # Output
        self.output_dir = ''

        # Read both default and user-defined settings
        self.read_config(path_to_ini_file)

    def read_config(self, path_to_ini_file):

        # Location of this code
        loc = os.path.dirname(os.path.abspath(__file__))

        # Load default settings
        default_config_path = os.path.join(loc, 'safe_default.ini')
        default_config = configparser.ConfigParser(allow_no_value=True, comment_prefixes=('#', ';', '{'))
        default_config.read_file(open(default_config_path))

        # Load user-defined settings, if any
        config = configparser.ConfigParser(defaults=default_config['DEFAULT'],
                                           allow_no_value=True,
                                           comment_prefixes=('#', ';', '{'))
        config.read(path_to_ini_file)

        if 'Input files' not in config:
            config['Input files'] = {}

        path_to_safe_data = config.get('Input files', 'safe_data')  # falls back on default if empty
        path_to_network_file = config.get('Input files', 'networkfile')  # falls back on default if empty
        path_to_attribute_file = config.get('Input files', 'annotationfile')  # falls back on default if empty

        self.path_to_safe_data = path_to_safe_data
        self.path_to_network_file = os.path.join(path_to_safe_data, path_to_network_file)
        self.path_to_attribute_file = os.path.join(path_to_safe_data, path_to_attribute_file)

        self.attribute_sign = config.get('Input files', 'annotationsign') # falls back on default if empty

        if 'Analysis parameters' not in config:
            config['Analysis parameters'] = {}
        self.node_distance_metric = config.get('Analysis parameters', 'nodeDistanceType')
        self.neighborhood_radius_type = config.get('Analysis parameters', 'neighborhoodRadiusType')
        self.neighborhood_radius = float(config.get('Analysis parameters', 'neighborhoodRadius'))

        self.attribute_unimodality_metric = config.get('Analysis parameters', 'unimodalityType')
        self.attribute_distance_metric = config.get('Analysis parameters', 'groupDistanceType')
        self.attribute_distance_threshold = float(config.get('Analysis parameters', 'groupDistanceThreshold'))

        self.output_dir = os.path.dirname(path_to_ini_file)
        if not self.output_dir:
            self.output_dir = loc

    def load_network(self, **kwargs):

        # Overwriting the global settings, if required
        if 'network_file' in kwargs:
            self.path_to_network_file = kwargs['network_file']
        if 'node_key_attribute' in kwargs:
            self.node_key_attribute = kwargs['node_key_attribute']

        [_, file_extension] = os.path.splitext(self.path_to_network_file)

        if self.verbose:
            print('Loading network from %s' % self.path_to_network_file)

        if file_extension == '.mat':
            self.graph = load_network_from_mat(self.path_to_network_file, verbose=self.verbose)
        elif file_extension == '.gpickle':
            self.graph = load_network_from_gpickle(self.path_to_network_file, verbose=self.verbose)
            self.node_key_attribute = 'label_orf'
        elif file_extension == '.txt':
            self.graph = load_network_from_txt(self.path_to_network_file, verbose=self.verbose)

        # Setting the node key for mapping attributes
        key_list = nx.get_node_attributes(self.graph, self.node_key_attribute)
        nx.set_node_attributes(self.graph, key_list, name='key')

    def load_attributes(self, **kwargs):

        # Overwrite the global settings, if required
        if 'attribute_file' in kwargs:
            self.path_to_attribute_file = kwargs['attribute_file']

        node_label_order = list(nx.get_node_attributes(self.graph, self.node_key_attribute).values())

        if self.verbose and isinstance(self.path_to_attribute_file, str):
            print('Loading attributes from %s' % self.path_to_attribute_file)

        [self.attributes, self.node2attribute] = load_attributes(self.path_to_attribute_file, node_label_order)

    def define_neighborhoods(self, **kwargs):

        # Overwriting the global settings, if required
        if 'node_distance_metric' in kwargs:
            self.node_distance_metric = kwargs['node_distance_metric']

        if 'neighborhood_radius_type' in kwargs:
            self.neighborhood_radius_type = kwargs['neighborhood_radius_type']

        if 'neighborhood_radius' in kwargs:
            self.neighborhood_radius = kwargs['neighborhood_radius']

        all_shortest_paths = {}

        if self.node_distance_metric == 'shortpath_weighted_layout':
            # x = np.matrix(self.graph.nodes.data('x'))[:, 1]
            x = list(dict(self.graph.nodes.data('x')).values())
            nr = self.neighborhood_radius * (np.max(x) - np.min(x))
            all_shortest_paths = dict(nx.all_pairs_dijkstra_path_length(self.graph,
                                                                        weight='length', cutoff=nr))
        elif self.node_distance_metric == 'shortpath':
            nr = self.neighborhood_radius
            all_shortest_paths = dict(nx.all_pairs_dijkstra_path_length(self.graph, cutoff=nr))

        neighbors = [(s, t) for s in all_shortest_paths for t in all_shortest_paths[s].keys()]

        neighborhoods = np.zeros([self.graph.number_of_nodes(), self.graph.number_of_nodes()], dtype=int)
        for i in neighbors:
            neighborhoods[i] = 1

        # Set diagonal to zero (a node is not part of its own neighborhood)
        # np.fill_diagonal(neighborhoods, 0)

        # Calculate the average neighborhood size
        num_neighbors = np.sum(neighborhoods, axis=1)

        if self.verbose:
            print('Node distance metric: %s' % self.node_distance_metric)
            print('Neighborhood definition: %.2f x %s' % (self.neighborhood_radius, self.neighborhood_radius_type))
            print('Number of nodes per neighborhood (mean +/- std): %.2f +/- %.2f' % (np.mean(num_neighbors), np.std(num_neighbors)))

        self.neighborhoods = neighborhoods

    def compute_pvalues(self, **kwargs):

        if 'how' in kwargs:
            self.enrichment_type = kwargs['how']

        # Determine if attributes are quantitative or binary
        num_other_values = np.sum(~np.isnan(self.node2attribute) & ~np.isin(self.node2attribute, [0, 1]))

        if self.enrichment_type == 'randomization':
            num_other_values = 100

        if num_other_values == 0:
            self.compute_pvalues_by_hypergeom(**kwargs)
        else:
            self.compute_pvalues_by_randomization(**kwargs)

        self.nes_binary = (self.nes > -np.log10(self.enrichment_threshold)).astype(int)
        self.attributes['num_neighborhoods_enriched'] = np.sum(self.nes_binary, axis=0)

    def compute_pvalues_by_randomization(self, **kwargs):

        print('The node attribute values appear to be quantitative. '
              'Using randomization to calculate enrichment...')

        def compute_neighborhood_score(neighborhood2node, node2attribute):

            with np.errstate(invalid='ignore', divide='ignore'):

                A = neighborhood2node
                B = np.where(~np.isnan(node2attribute), node2attribute, 0)

                NA = A
                NB = np.where(~np.isnan(node2attribute), 1, 0)

                AB = np.dot(A, B)
                N = np.dot(NA, NB)

                M = AB / N

                A2B2 = np.dot(np.power(A, 2), np.power(B, 2))
                MAB = M * AB
                M2 = N * np.power(M, 2)

                std = np.sqrt(A2B2 - 2 * MAB + M2)

                neighborhood_score = AB / std

            return neighborhood_score

        if 'num_permutations' in kwargs:
            self.num_permutations = kwargs['num_permutations']

        N_in_neighborhood_in_group = compute_neighborhood_score(self.neighborhoods, self.node2attribute)

        n2a = self.node2attribute
        indx_vals = np.nonzero(np.sum(~np.isnan(n2a), axis=1))[0]

        counts_neg = np.zeros(N_in_neighborhood_in_group.shape)
        counts_pos = np.zeros(N_in_neighborhood_in_group.shape)
        for _ in tqdm(np.arange(self.num_permutations)):

            # Permute the rows that have values
            n2a[indx_vals, :] = n2a[np.random.permutation(indx_vals), :]

            N_in_neighborhood_in_group_perm = compute_neighborhood_score(self.neighborhoods, n2a)

            with np.errstate(invalid='ignore', divide='ignore'):
                counts_neg = np.add(counts_neg, N_in_neighborhood_in_group_perm < N_in_neighborhood_in_group)
                counts_pos = np.add(counts_pos, N_in_neighborhood_in_group_perm > N_in_neighborhood_in_group)

        self.pvalues_neg = counts_neg / self.num_permutations
        self.pvalues_pos = counts_pos / self.num_permutations

        # Log-transform into neighborhood enrichment scores (NES)
        # Necessary conservative adjustment: when p-value = 0, set it to 1/num_permutations
        nes_pos = -np.log10(np.where(self.pvalues_pos > 0, self.pvalues_pos, 1/self.num_permutations))
        nes_neg = -np.log10(np.where(self.pvalues_neg > 0, self.pvalues_neg, 1/self.num_permutations))

        if self.attribute_sign == 'highest':
            self.nes = nes_pos
        elif self.attribute_sign == 'lowest':
            self.nes = nes_neg
        elif self.attribute_sign == 'both':
            self.nes = nes_pos - nes_neg

    def compute_pvalues_by_hypergeom(self, multiple_testing=True, **kwargs):

        print('The node attribute values appear to be binary. '
              'Using the hypergeometric test to calculate enrichment...')

        N = np.zeros([self.graph.number_of_nodes(), len(self.attributes)]) + self.graph.number_of_nodes()
        N_in_group = np.tile(np.nansum(self.node2attribute, axis=0), (self.graph.number_of_nodes(), 1))

        N_in_neighborhood = np.tile(np.sum(self.neighborhoods, axis=0)[:, np.newaxis], (1, len(self.attributes)))

        N_in_neighborhood_in_group = np.dot(self.neighborhoods,
                                            np.where(~np.isnan(self.node2attribute), self.node2attribute, 0))

        self.pvalues_pos = hypergeom.sf(N_in_neighborhood_in_group - 1, N, N_in_group, N_in_neighborhood)

        # Correct for multiple testing
        if multiple_testing:
            self.pvalues_pos = self.pvalues_pos * self.attributes.shape[0]
            self.pvalues_pos[self.pvalues_pos > 1] = 1

        # Log-transform into neighborhood enrichment scores (NES)
        self.nes = -np.log10(self.pvalues_pos)

    def define_top_attributes(self, **kwargs):

        self.attributes['top'] = False

        # Requirement 1: a minimum number of enriched neighborhoods
        self.attributes.loc[
            self.attributes['num_neighborhoods_enriched'] >= self.attribute_enrichment_min_size, 'top'] = True

        # Requirement 2: 1 connected component in the subnetwork of enriched neighborhoods
        if self.attribute_unimodality_metric == 'connectivity':
            self.attributes['num_connected_components'] = 0
            for attribute in self.attributes.index.values[self.attributes['top'] == 1]:
                enriched_neighborhoods = list(compress(list(self.graph), self.nes_binary[:, attribute] > 0))
                H = nx.subgraph(self.graph, enriched_neighborhoods)
                self.attributes.loc[attribute, 'num_connected_components'] = nx.number_connected_components(H)

            # Exclude attributes that have more than 1 connected component
            self.attributes.loc[self.attributes['num_connected_components'] > 1, 'top'] = False

        if self.verbose:
            print('Number of top attributes: %d' % np.sum(self.attributes['top']))

    def define_domains(self, **kwargs):

        # Overwriting global settings, if necessary
        if 'attribute_distance_threshold' in kwargs:
            self.attribute_distance_threshold = kwargs['attribute_distance_threshold']

        m = self.nes_binary[:, self.attributes['top']].T
        Z = linkage(m, method='average', metric=self.attribute_distance_metric)
        max_d = np.max(Z[:, 2] * self.attribute_distance_threshold)
        domains = fcluster(Z, max_d, criterion='distance')

        self.attributes['domain'] = 0
        self.attributes.loc[self.attributes['top'], 'domain'] = domains

        # Assign nodes to domains
        node2nes = pd.DataFrame(data=self.nes,
                                    columns=[self.attributes.index.values, self.attributes['domain']])
        node2nes_binary = pd.DataFrame(data=self.nes_binary,
                                           columns=[self.attributes.index.values, self.attributes['domain']])

        # # A node belongs to the domain that contains the attribute
        # for which the node has the highest enrichment
        # self.node2domain = node2es.groupby(level='domain', axis=1).max()
        # t_max = self.node2domain.loc[:, 1:].max(axis=1)
        # t_idxmax = self.node2domain.loc[:, 1:].idxmax(axis=1)
        # t_idxmax[t_max < -np.log10(self.enrichment_threshold)] = 0

        # A node belongs to the domain that contains the highest number of attributes
        # for which the nodes is significantly enriched
        self.node2domain = node2nes_binary.groupby(level='domain', axis=1).sum()
        t_max = self.node2domain.loc[:, 1:].max(axis=1)
        t_idxmax = self.node2domain.loc[:, 1:].idxmax(axis=1)
        t_idxmax[t_max == 0] = 0

        self.node2domain['primary_domain'] = t_idxmax

        # Get the max NES for the primary domain
        o = node2nes.groupby(level='domain', axis=1).max()
        i = pd.Series(t_idxmax)
        self.node2domain['primary_nes'] = o.lookup(i.index, i.values)

        if self.verbose:
            num_domains = len(np.unique(domains))
            num_attributes_per_domain = self.attributes.loc[self.attributes['domain'] > 0].groupby('domain')['id'].count()
            min_num_attributes = num_attributes_per_domain.min()
            max_num_attributes = num_attributes_per_domain.max()
            print('Number of domains: %d (containing %d-%d attributes)' %
                  (num_domains, min_num_attributes, max_num_attributes))

    def trim_domains(self, **kwargs):

        # Remove domains that are the top choice for less than a certain number of neighborhoods
        domain_counts = np.zeros(len(self.attributes['domain'].unique())).astype(int)
        t = self.node2domain.groupby('primary_domain')['primary_domain'].count()
        domain_counts[t.index] = t.values
        to_remove = np.flatnonzero(domain_counts < self.attribute_enrichment_min_size)

        self.attributes.loc[self.attributes['domain'].isin(to_remove), 'domain'] = 0

        idx = self.node2domain['primary_domain'].isin(to_remove)
        self.node2domain.loc[idx, ['primary_domain', 'primary_nes']] = 0

        # Rename the domains (simple renumber)
        a = np.sort(self.attributes['domain'].unique())
        b = np.arange(len(a))
        renumber_dict = dict(zip(a, b))

        self.attributes['domain'] = [renumber_dict[k] for k in self.attributes['domain']]
        self.node2domain['primary_domain'] = [renumber_dict[k] for k in self.node2domain['primary_domain']]

        # Make labels for each domain
        domains = np.sort(self.attributes['domain'].unique())
        domains_labels = self.attributes.groupby('domain')['name'].apply(chop_and_filter)
        self.domains = pd.DataFrame(data={'id': domains, 'label': domains_labels})
        self.domains.set_index('id', drop=False)

        if self.verbose:
            print('Removed %d domains because they were the top choice for less than %d neighborhoods.'
                  % (len(to_remove), self.attribute_enrichment_min_size))

    def plot_network(self):

        plot_network(self.graph)

    def plot_composite_network(self, show_each_domain=False, show_domain_ids=True):

        domains = np.sort(self.attributes['domain'].unique())

        # Define colors per domain
        domain2rgb = get_colors('hsv', len(domains))

        # Store domain info
        self.domains['rgba'] = domain2rgb.tolist()

        # Compute composite node colors
        node2nes = pd.DataFrame(data=self.nes,
                                    columns=[self.attributes.index.values, self.attributes['domain']])

        node2nes_binary = pd.DataFrame(data=self.nes_binary,
                                           columns=[self.attributes.index.values, self.attributes['domain']])
        node2domain_count = node2nes_binary.groupby(level='domain', axis=1).sum()
        node2all_domains_count = node2domain_count.sum(axis=1)[:, np.newaxis]

        with np.errstate(divide='ignore', invalid='ignore'):
            c = np.matmul(node2domain_count, domain2rgb) / node2all_domains_count

        t = np.sum(c, axis=1)
        c[np.isnan(t) | np.isinf(t), :] = [0, 0, 0, 0]

        # Sort nodes by their overall brightness
        ix = np.argsort(np.sum(c, axis=1))

        x = dict(self.graph.nodes.data('x'))
        y = dict(self.graph.nodes.data('y'))

        ds = [x, y]
        pos = {}
        for k in x:
            pos[k] = np.array([d[k] for d in ds])

        pos2 = np.vstack(list(pos.values()))

        # Figure parameters
        num_plots = 2

        if show_each_domain:
            num_plots = num_plots + (len(domains) - 1)

        nrows = int(np.ceil(num_plots/2))
        ncols = np.min([num_plots, 2])
        figsize = (10 * ncols, 10 * nrows)

        [fig, axes] = plt.subplots(nrows=nrows, ncols=ncols, figsize=figsize, sharex=True, sharey=True,
                                   facecolor='#000000')
        axes = axes.ravel()

        # First, plot the network
        ax = axes[0]
        ax = plot_network(self.graph, ax=ax)

        # Then, plot the composite network
        axes[1].scatter(pos2[ix, 0], pos2[ix, 1], c=c[ix], s=60, edgecolor=None)
        axes[1].set_aspect('equal')
        axes[1].set_facecolor('#000000')

        # Plot a circle around the network
        plot_network_contour(self.graph, axes[1])

        # Then, plot each domain separately, if requested
        if show_each_domain:
            for domain in domains[domains > 0]:
                domain_color = np.reshape(domain2rgb[domain, :], (1, 4))

                alpha = node2nes.loc[:, domain].values
                alpha = alpha / self.enrichment_max_log10
                alpha[alpha > 1] = 1
                alpha = np.reshape(alpha, -1)

                c = np.repeat(domain_color, len(alpha), axis=0)
                # c[:, 3] = alpha

                idx = self.node2domain['primary_domain'] == domain
                # ix = np.argsort(c)
                axes[1+domain].scatter(pos2[idx, 0], pos2[idx, 1], c=c[idx],
                                       s=60, edgecolor=None)

                if show_domain_ids:
                    centroid_x = np.nanmean(pos2[idx, 0])
                    centroid_y = np.nanmean(pos2[idx, 1])
                    axes[1].text(centroid_x, centroid_y, str(domain),
                                 fontdict={'size': 16, 'color': 'white', 'weight': 'bold'})

                axes[1+domain].set_aspect('equal')
                axes[1+domain].set_facecolor('#000000')
                axes[1+domain].set_title('Domain %d\n%s' % (domain, self.domains.loc[domain, 'label']),
                                         color='#ffffff')
                plot_network_contour(self.graph, axes[1+domain])

        fig.set_facecolor("#000000")

    def plot_sample_attributes(self, attributes=1, top_attributes_only=False,
                               show_costanzo2016=False, show_costanzo2016_legend=True,
                               show_raw_data=False, show_significant_nodes=False,
                               show_colorbar=False,
                               labels=[],
                               save_fig=None, **kwargs):

        all_attributes = self.attributes.index.values
        if top_attributes_only:
            all_attributes = all_attributes[self.attributes['top']]

        if isinstance(attributes, int):
            attributes = np.random.choice(all_attributes, attributes, replace=False)

        x = dict(self.graph.nodes.data('x'))
        y = dict(self.graph.nodes.data('y'))

        ds = [x, y]
        pos = {}
        for k in x:
            pos[k] = np.array([d[k] for d in ds])

        pos2 = np.vstack(list(pos.values()))

        # Figure parameters
        nrows = int(np.ceil((len(attributes)+1)/2))
        ncols = np.min([len(attributes)+1, 2])
        figsize = (10*ncols, 10*nrows)

        [fig, axes] = plt.subplots(nrows=nrows, ncols=ncols, figsize=figsize, sharex=True, sharey=True)
        axes = axes.ravel()

        midrange = [np.log10(0.05), 0, -np.log10(0.05)]

        # First, plot the network
        ax = axes[0]
        ax = plot_network(self.graph, ax=ax)

        # Plot the attribute
        for idx_attribute, attribute in enumerate(attributes):

            ax = axes[idx_attribute+1]

            # Dynamically determine the min & max of the colorscale
            if 'vmin' in kwargs:
                vmin = kwargs['vmin']
            else:
                vmin = np.nanmin([np.log10(1 / self.num_permutations), np.nanmin(-np.abs(self.nes[:, attribute]))])
            if 'vmax' in kwargs:
                vmax = kwargs['vmax']
            else:
                vmax = np.nanmax([-np.log10(1 / self.num_permutations), np.nanmax(np.abs(self.nes[:, attribute]))])

            # Determine the order of points, such that the brightest ones are on top
            idx = np.argsort(np.abs(self.nes[:, attribute]))

            # Colormap
            colors_hex = ['82add6', '000000', '000000', '000000', 'facb66']
            colors_rgb = [tuple(int(c[i:i+2], 16)/255 for i in (0, 2, 4)) for c in colors_hex]

            cmap = LinearSegmentedColormap.from_list('my_cmap', colors_rgb)

            sc = ax.scatter(pos2[idx, 0], pos2[idx, 1], c=self.nes[idx, attribute], vmin=vmin, vmax=vmax,
                            s=60, cmap=cmap, norm=MidpointRangeNormalize(midrange=midrange, vmin=vmin, vmax=vmax),
                            edgecolors=None)

            if show_colorbar:
                cb = ax.figure.colorbar(sc, ax=ax,
                                        orientation='horizontal',
                                        pad=0.05,
                                        shrink=0.75,
                                        ticks=[vmin, midrange[0], midrange[1], midrange[2], vmax],
                                        drawedges=False)

                # set colorbar label plus label color
                cb.set_label('Enrichment p-value', color='w')

                # set colorbar tick color
                cb.ax.xaxis.set_tick_params(color='w')

                # set colorbar edgecolor
                cb.outline.set_edgecolor('white')
                cb.outline.set_linewidth(1)

                # set colorbar ticklabels
                plt.setp(plt.getp(cb.ax.axes, 'xticklabels'), color='w')

                cb.ax.set_xticklabels([format(r'$10^{%d}$' % vmin),
                                       r'$10^{-2}$', r'$1$', r'$10^{-2}$',
                                       format(r'$10^{-%d}$' % vmax)])

            if show_raw_data:
                s_min = 2
                s_max = 45
                n = self.node2attribute[:, attribute]
                n = np.where(~np.isnan(n), n, 0)

                n2a = np.abs(n)
                a = (s_max-s_min)/(np.nanmax(n2a)-np.nanmin(n2a))
                b = s_min - a*np.nanmin(n2a)
                s = a * n2a + b

                sgn = np.sign(n)
                sgn = np.where(np.isnan(sgn), 0, sgn)
                sgn = sgn.astype(int) + 1

                alpha = np.abs(sgn-1).astype(float)

                # Colormap
                clrs_labels = ['negative', 'zero', 'positive']
                clrs = [(1, 0, 0), (0, 0, 1), (0, 1, 0)]

                ax.scatter(pos2[:, 0], pos2[:, 1], s=s, c=np.array(clrs)[sgn], marker='.')

                stp = (np.nanmax(pos2[:, 0])-np.nanmin(pos2[:, 0]))/2
                for ix_c, c in enumerate(clrs):
                    txt_x = np.nanmin(pos2[:, 0]) + ix_c*stp
                    txt_y = np.nanmax(pos2[:, 1]) + stp*0.1
                    plt.text(txt_x, txt_y, clrs_labels[ix_c], fontdict={'color': c})

            if show_significant_nodes:

                if self.attribute_sign in ['highest', 'both']:
                    idx = self.nes[:, attribute] > -np.log10(self.enrichment_threshold)
                    ax.scatter(pos2[idx, 0], pos2[idx, 1], c='g', marker='+')

                if self.attribute_sign in ['lowest', 'both']:
                    idx = self.nes[:, attribute] < np.log10(self.enrichment_threshold)
                    ax.scatter(pos2[idx, 0], pos2[idx, 1], c='r', marker='+')

            if show_costanzo2016:
                plot_costanzo2016_network_annotations(self.graph, ax, self.path_to_safe_data)

            # Plot a circle around the network
            plot_network_contour(self.graph, ax)

            if labels:
                plot_labels(labels, self.graph, ax)

            ax.set_aspect('equal')
            ax.set_facecolor('#000000')

            ax.grid(False)
            ax.margins(0.1, 0.1)

            title = self.attributes.loc[attribute, 'name']

            title = '\n'.join(textwrap.wrap(title, width=30))
            ax.set_title(title, color='#ffffff')

            plt.axis('off')

        fig.set_facecolor("#000000")

        if save_fig:
            path_to_fig = os.path.join(self.output_dir, save_fig)
            print('Output path: %s' % path_to_fig)
            plt.savefig(path_to_fig, facecolor='k')

        return ax

    def print_output_files(self, **kwargs):

        print('Output path: %s' % self.output_dir)

        # Domain properties
        path_domains = os.path.join(self.output_dir, 'domain_properties_annotation.txt')
        if self.domains is not None:
            self.domains.drop(labels=[0], axis=0, inplace=True, errors='ignore')
            self.domains.to_csv(path_domains, sep='\t')

        # Attribute properties
        path_attributes = os.path.join(self.output_dir, 'attribute_properties_annotation.txt')
        self.attributes.to_csv(path_attributes, sep='\t')

        # Node properties
        path_nodes = os.path.join(self.output_dir, 'node_properties_annotation.txt')

        t = nx.get_node_attributes(self.graph, 'key')
        ids = list(t.keys())
        keys = list(t.values())
        t = nx.get_node_attributes(self.graph, 'label')
        labels = list(t.values())
        if self.node2domain is not None:
            domains = self.node2domain['primary_domain'].values
            ness = self.node2domain['primary_nes'].values
            num_domains = self.node2domain[self.domains['id']].sum(axis=1).values
            self.nodes = pd.DataFrame(data={'id': ids, 'key': keys, 'label': labels, 'domain': domains,
                                            'nes': ness, 'num_domains': num_domains})
        else:

            self.nodes = pd.DataFrame(self.nes)
            self.nodes.columns = self.attributes['name']
            self.nodes.insert(loc=0, column='key', value=keys)
            self.nodes.insert(loc=1, column='label', value=labels)

        self.nodes.to_csv(path_nodes, sep='\t')


# def run_safe_batch(sf, attribute_file):
#
#     print('Loading attributes')
#     sf.load_attributes(attribute_file=attribute_file)
#
#     print('Computing p-values')
#     sf.compute_pvalues(num_permutations=1000)
#
#     return sf


if __name__ == '__main__':

    start = time.time()

    parser = argparse.ArgumentParser(description='Run Spatial Analysis of Functional Enrichment (SAFE) on the default Costanzo et al., 2016 network')
    parser.add_argument('path_to_attribute_file', metavar='path_to_attribute_file', type=str,
                        help='Path to the file containing label-to-attribute annotations')

    args = parser.parse_args()

    nr_processes = mp.cpu_count()

    sf = SAFE()
    sf.load_network()
    sf.define_neighborhoods()

    print('Loading attributes')
    sf.load_attributes(attribute_file=args.path_to_attribute_file)

    print('Computing p-values')
    sf.compute_pvalues(num_permutations=1000)

    # # Break the list into smaller chunks of 200 images and process the chunks sequentially
    # chunk_size = int(np.ceil(attributes.shape[1]/nr_processes))
    # chunks = np.arange(0, attributes.shape[1], chunk_size)
    #
    # print(chunk_size)
    #
    # all_data = pd.DataFrame()
    #
    # for ix_chunk in chunks:
    #     ix_chunk_start = ix_chunk
    #     ix_chunk_stop = np.min([ix_chunk + chunk_size - 1, attributes.shape[1]]) + 1
    #
    #     attributes_this = attributes.iloc[:, ix_chunk_start:ix_chunk_stop]
    #
    #     pool = mp.Pool(processes=nr_processes)
    #     func = partial(run_safe_batch, sf)
    #
    #     for res in pool.map_async(func, attributes_this).get():
    #         all_data = np.concatenate((all_data, res.es), axis=1)
    #
    #     print('Execution time: %.2f seconds' % (time.time() - start))

    output_file = format('%s_safe_nes.p' % args.path_to_attribute_file)

    with open(output_file, 'wb') as handle:
        pickle.dump(sf.nes, handle)
