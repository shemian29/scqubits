# symbolic_circuit.py
#
# This file is part of scqubits.
#
#    Copyright (c) 2019 and later, Jens Koch and Peter Groszkowski
#    All rights reserved.
#
#    This source code is licensed under the BSD-style license found in the
#    LICENSE file in the root directory of this source tree.
############################################################################

import copy
import itertools
import warnings

from symtable import Symbol
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import numpy as np
import scipy as sp
import scqubits.io_utils.fileio_serializers as serializers
import scqubits.settings as settings
import sympy
import yaml

from numpy import ndarray
from scqubits.utils.misc import flatten_list, is_float_string
from sympy import symbols


def process_word(word: str) -> Union[float, symbols]:
    if is_float_string(word):
        return float(word)
    return symbols(word)


def parse_branch_parameters(
    words: List[str], branch_type: str
) -> Tuple[List[float], Dict[Symbol, float]]:
    """
    Parses the branch parameters depending on the branch type.

    Parameters
    ----------
    words:
        list of strings from which parameters need to be parsed
    branch_type:
        str denoting the type of the branch

    Returns
    -------
    branch_params
        List of parameters which will be used to initiate a Branch object
    branch_var_dict
        A dictionary of variables defined for this current branch.

    Raises
    ------
    Exception
        An exception is raised if the proper syntax is not followed when using variables
        in the input file.
    """
    branch_var_dict: Dict[Symbol, float] = {}
    branch_params: List[float] = []
    num_params = 2 if branch_type in ["JJ", "JJ2"] else 1
    for word in words[0:num_params]:
        if not is_float_string(word):
            if len(word.split("=")) > 2:
                raise Exception("Syntax error in branch specification.")
            if len(word.split("=")) == 2:
                var_str, init_val = word.split("=")
                params = [process_word(var_str), process_word(init_val)]
            elif len(word.split("=")) == 1:
                params = [process_word(word)]
        else:
            params = [float(word)]

        if len(params) == 1:
            branch_params.append(params[0])
        else:
            branch_var_dict[params[0]] = params[1]
            branch_params.append(params[0])

    return branch_params, branch_var_dict


class Node:
    """
    Class representing a circuit node, and handled by `Circuit`. The attribute
    `<Node>.branches` is a list of `Branch` objects containing all branches connected to
    the node.

    Parameters
    ----------
    id: int
        integer identifier of the node
    marker: int
        An internal attribute used to group nodes and identify sub-circuits in the
        method independent_modes.
    """

    def __init__(self, index: int, marker: int):
        self.index = index
        self.marker = marker
        self._init_params = {"id": self.index, "marker": self.marker}
        self.branches: List[Branch] = []

    def __str__(self) -> str:
        return "Node {}".format(self.index)

    def __repr__(self) -> str:
        return "Node({})".format(self.index)

    def connected_nodes(self, branch_type: str) -> List["Node"]:
        """
        Returns a list of all nodes directly connected by branches to the current
        node, either considering all branches or a specified `branch_type`:
        "C", "L", "JJ", "all" for capacitive, inductive, Josephson junction,
        or all types of branches.
        """
        result = []
        if branch_type == "all":
            branch_list = self.branches
        else:
            branch_list = [
                branch for branch in self.branches if branch.type == branch_type
            ]
        for branch in branch_list:
            if branch.nodes[0].index == self.index:
                result.append(branch.nodes[1])
            else:
                result.append(branch.nodes[0])
        return result

    def is_ground(self) -> bool:
        """
        Returns a bool if the node is a ground node. It is ground if the id is set to 0.
        """
        return True if self.index == 0 else False

    def __deepcopy__(self, memo):
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        for k, v in self.__dict__.items():
            setattr(result, k, copy.deepcopy(v, memo))
        return result


class Branch:
    """
    Class describing a circuit branch, used in the Circuit class.

    Parameters
    ----------
    n_i, n_f:
        initial and final nodes connected by this branch;
    branch_type:
        is the type of this Branch, example "C","JJ" or "L"
    parameters:
        dictionary of parameters for the branch, namely for
        capacitance: {"EC":  <value>};
        for inductance: {"EL": <value>};
        for Josephson Junction: {"EJ": <value>, "ECJ": <value>}

    Examples
    --------
    `Branch("C", Node(1, 0), Node(2, 0))`
    is a capacitive branch connecting the nodes with indices 0 and 1.
    """

    def __init__(
        self,
        n_i: Node,
        n_f: Node,
        branch_type: str,
        parameters: Optional[List[Union[float, Symbol, int]]] = None,
        id_str: str = None,
    ):
        self.nodes = (n_i, n_f)
        self.type = branch_type
        self.parameters = parameters
        self.id_str = id_str
        # store info of current branch inside the provided nodes
        # setting the parameters if it is provided
        if parameters is not None:
            self.set_parameters(parameters)

        self.nodes[0].branches.append(self)
        self.nodes[1].branches.append(self)

    def __str__(self) -> str:
        return (
            "Branch "
            + self.type
            + " connecting nodes: ("
            + str(self.nodes[0].index)
            + ","
            + str(self.nodes[1].index)
            + "); "
            + str(self.parameters)
        )

    def __repr__(self) -> str:
        return f"Branch({self.type}, {self.nodes[0].index}, {self.nodes[1].index}, id_str: {self.id_str})"

    def set_parameters(self, parameters) -> None:
        if self.type in ["C", "L"]:
            self.parameters = {f"E{self.type}": parameters[0]}
        elif self.type in ["JJ", "JJ2"]:
            self.parameters = {"EJ": parameters[0], "ECJ": parameters[1]}

    def node_ids(self) -> Tuple[int, int]:
        return self.nodes[0].index, self.nodes[1].index

    def is_connected(self, branch) -> bool:
        """Returns a boolean indicating whether the current branch is
        connected to the given `branch`"""
        distinct_node_count = len(set(self.nodes + branch.nodes))
        if distinct_node_count < 4:
            return True
        return False

    def common_node(self, branch) -> Set[Node]:
        """Returns the common nodes between self and the `branch` given as input"""
        return set(self.nodes) & set(branch.nodes)

    def __deepcopy__(self, memo):
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        for k, v in self.__dict__.items():
            setattr(result, k, copy.deepcopy(v, memo))
        return result


class SymbolicCircuit(serializers.Serializable):
    r"""
    Describes a circuit consisting of nodes and branches.

    Examples
    --------
    For a transmon qubit, the input file reads:
        ```
        # file_name: transmon_num.inp
        nodes: 2
        branches:
        C	1,2	1
        JJ	1,2	1	10
        ```

    The `Circuit` object can be initiated using:
        `Circuit.from_input_file("transmon_num.inp")`

    Parameters
    ----------
    nodes_list: List[Nodes]
        List of nodes in the circuit
    branches_list: List[Branch]
        List of branches connecting the above set of nodes.
    basis_completion: str
        choices are: "heuristic" (default) or "canonical"; selects type of basis for
        completing the transformation matrix.
    ground_node: Node
        If the circuit is grounded, the ground node is treated separately and should be
        provided to this parameter.
    is_flux_dynamic: bool
        set to False by default. Indicates if the flux allocation is done by assuming
        that flux is time dependent. When set to True, it disables the option to change
        the closure branches.
    initiate_sym_calc: bool
        set to True by default. Initiates the object attributes by calling the
        function initiate_symboliccircuit method when set to True.
    identify_LC_variables: bool
            set to True by default. If set to True, the extended variables that only
            appears in the quadratic Hamiltonian is identified.
    """

    def __init__(
        self,
        nodes_list_without_ground: List[Node],
        branches_list: List[Branch],
        branch_var_dict: Dict[Union[Any, Symbol], Union[Any, float]],
        basis_completion: str = "heuristic",
        ground_node: Optional[Node] = None,
        is_flux_dynamic: bool = True,
        initiate_sym_calc: bool = True,
        input_string: str = "",
        identify_LC_variables: bool = True,
    ):
        self.branches = branches_list
        self._node_list_without_ground = nodes_list_without_ground
        self.nodes = nodes_list_without_ground
        self.input_string = input_string

        self._sys_type = type(self).__name__  # for object description

        # attributes set by methods
        self.transformation_matrix: Optional[ndarray] = None
        self.orthogonalized_transformation_matrix: Optional[ndarray] = None

        self.var_categories: Optional[List[int]] = None
        self.island_node_dict: Optional[Dict[str, Union[list, None]]] = None
        self.external_fluxes: List[Symbol] = []
        self.closure_branches: List[Branch] = []

        self.symbolic_params: Dict[Symbol, float] = dict(
            zip(list(branch_var_dict.keys()), list(branch_var_dict.values()))
        )

        self.hamiltonian_symbolic: Optional[sympy.Expr] = None
        # to store the internally used lagrangian
        self._lagrangian_symbolic: Optional[sympy.Expr] = None
        self.lagrangian_symbolic: Optional[sympy.Expr] = None
        # symbolic lagrangian in terms of untransformed generalized flux variables
        self.lagrangian_node_vars: Optional[sympy.Expr] = None
        # symbolic expression for potential energy
        self.potential_symbolic: Optional[sympy.Expr] = None
        self.potential_node_vars: Optional[sympy.Expr] = None

        # parameters for grounding the circuit
        self.ground_node = ground_node
        self.is_grounded = bool(self.ground_node)

        if self.is_grounded:
            self.nodes = [self.ground_node] + self.nodes

        # switch to control the dynamic flux allocation in the loops
        self.is_flux_dynamic = is_flux_dynamic

        # parameter for choosing matrix used for basis completion in the variable
        # transformation matrix
        self.basis_completion = (
            basis_completion  # default, the other choice is standard
        )

        self.initiate_sym_calc = initiate_sym_calc

        # Calling the function to initiate the class variables
        if initiate_sym_calc:
            self.configure(identify_LC_variables=identify_LC_variables)

    def is_any_branch_parameter_symbolic(self):
        return True if len(self.symbolic_params) > 0 else False

    @staticmethod
    def _gram_schmidt(initial_vecs: ndarray, metric: ndarray) -> ndarray:
        def inner_product(u, v, metric):
            return u @ metric @ v

        def projection(u, v, metric):
            """
            Projection of u on v

            Parameters
            ----------
            u : ndarray
            v : ndarray
            """
            return v * inner_product(v, u, metric) / inner_product(v, v, metric)

        orthogonal_vecs = [initial_vecs[0]]
        for i in range(1, len(initial_vecs)):
            vec = initial_vecs[i]
            projection_on_orthovecs = sum(
                [projection(vec, ortho_vec, metric) for ortho_vec in orthogonal_vecs]
            )
            orthogonal_vecs.append(vec - projection_on_orthovecs)
        return np.array(orthogonal_vecs).T

    def _orthogonalize_degenerate_eigen_vecs(
        self, evecs: ndarray, eigs: ndarray, relevant_eig_indices, cap_matrix: ndarray
    ) -> ndarray:
        relevant_eigs = eigs[relevant_eig_indices]
        unique_eigs = np.unique(np.round(relevant_eigs, 10))
        close_eigs = [
            list(np.where(np.abs(eigs - eig) < 1e-10)[0]) for eig in unique_eigs
        ]
        degenerate_indices_list = [
            indices for indices in close_eigs if len(indices) > 1
        ]

        orthogonal_evecs = evecs.copy()

        for degenerate_set in degenerate_indices_list:
            orthogonal_evecs[:, degenerate_set] = self._gram_schmidt(
                evecs[:, degenerate_set].T, metric=cap_matrix
            )

        return orthogonal_evecs

    def purely_harmonic_transformation(self) -> Tuple[ndarray, ndarray]:

        trans_mat, _ = self.variable_transformation_matrix()
        c_mat = (
            trans_mat.T @ self._capacitance_matrix(substitute_params=True) @ trans_mat
        )
        l_mat = (
            trans_mat.T @ self._inductance_matrix(substitute_params=True) @ trans_mat
        )
        if not self.is_grounded:
            c_mat = c_mat[:-1, :-1]
            l_mat = l_mat[:-1, :-1]
        normal_mode_freqs, normal_mode_vecs = sp.linalg.eig(l_mat, c_mat)
        normal_mode_freqs = normal_mode_freqs.round(10)
        # rounding to the tenth digit to remove numerical errors in eig calculation
        # rearranging the vectors
        idx = normal_mode_freqs.argsort()
        normal_freq_ids = [
            id
            for id in idx
            if normal_mode_freqs[id] != 0 and not np.isinf(normal_mode_freqs[id])
        ]
        zero_freq_ids = [id for id in idx if normal_mode_freqs[id] == 0]
        inf_freq_ids = [id for id in idx if np.isinf(normal_mode_freqs[id])]
        idx = normal_freq_ids + zero_freq_ids + inf_freq_ids
        # sorting so that all the zero frequencies show up at the end

        normal_mode_freqs = normal_mode_freqs[idx]
        normal_mode_vecs = normal_mode_vecs[:, idx]

        orthogonalized_normal_mode_vecs = self._orthogonalize_degenerate_eigen_vecs(
            normal_mode_vecs, normal_mode_freqs, range(len(normal_freq_ids)), c_mat
        )

        # constructing the new transformation
        trans_mat_new = trans_mat.copy()
        trans_mat_new[:, : len(c_mat)] = (
            trans_mat[:, : len(c_mat)] @ orthogonalized_normal_mode_vecs
        )

        return (
            np.real(
                np.sqrt(
                    [
                        freq
                        for freq in normal_mode_freqs
                        if not np.isinf(freq) and freq != 0
                    ]
                )
            ),
            trans_mat_new,
        )

    def configure(
        self,
        transformation_matrix: ndarray = None,
        closure_branches: List[Branch] = None,
        identify_LC_variables: bool = True,
    ):
        """
        Method to initialize the CustomQCircuit instance and initialize all the
        attributes needed before it can be passed on to AnalyzeQCircuit.

        Parameters
        ----------
        transformation_matrix:
            array used to set a transformation matrix other than the one generated by
            the method `variable_transformation_matrix`.
        closure_branches:
            List of branches for which the external flux variables will be defined.
        identify_LC_variables:
            set to True by default. If set to True, the extended variables that only
            appears in the quadratic Hamiltonian is identified.
        """
        # if the circuit is purely harmonic, then store the eigenfrequencies
        branch_type_list = [branch.type for branch in self.branches]
        self.is_purely_harmonic = (
            "JJ" not in branch_type_list and "JJ2" not in branch_type_list
        )

        if self.is_purely_harmonic:
            (
                self.normal_mode_freqs,
                transformation_matrix_normal_mode,
            ) = self.purely_harmonic_transformation()
            if transformation_matrix is None:
                transformation_matrix = transformation_matrix_normal_mode

        # if the user provides a transformation matrix
        if transformation_matrix is not None:
            self.var_categories = self.check_transformation_matrix(
                transformation_matrix, enable_warnings=not self.is_purely_harmonic
            )
            self.transformation_matrix = transformation_matrix
        # calculate the transformation matrix and identify the boundary conditions if
        # the user does not provide a custom transformation matrix
        else:
            (
                self.transformation_matrix,
                self.var_categories,
            ) = self.variable_transformation_matrix(
                identify_LC_variables=identify_LC_variables
            )
            (
                self.orthogonalized_transformation_matrix,
                self.island_node_dict,
            ) = self.orthogonalize_island_vectors()

        # find the closure branches in the circuit
        self.closure_branches = closure_branches or self._closure_branches()
        # setting external flux and offset charge variables
        self._set_external_fluxes(closure_branches=closure_branches)
        self._set_offset_charges()
        # setting the branch parameter variables
        # Calculate the Lagrangian
        (
            self._lagrangian_symbolic,
            self.potential_symbolic,
            self.lagrangian_node_vars,
            self.potential_node_vars,
        ) = self.generate_symbolic_lagrangian()

        # replacing energies with capacitances in the kinetic energy of the Lagrangian
        (
            self.lagrangian_symbolic,
            self.lagrangian_node_vars,
        ) = self._replace_energies_with_capacitances_L()

        # calculating the Hamiltonian directly when the number of nodes is less than 3
        if (
            len(self.nodes) <= settings.SYM_INVERSION_MAX_NODES
        ):  # only calculate the symbolic hamiltonian when the number of nodes is less
            # than 3. Else, the calculation will be skipped to the end when numerical
            # Hamiltonian of the circuit is requested.
            self.hamiltonian_symbolic = self.generate_symbolic_hamiltonian()

    def _replace_energies_with_capacitances_L(self):
        """
        Method replaces the energies in the Lagrangian with capacitances which are
        arbitrarily generated to make sure that the Lagrangian looks dimensionally
        correct.
        """
        # Replacing energies with capacitances if any branch parameters are symbolic
        L = self._lagrangian_symbolic.expand()
        L_old = self.lagrangian_node_vars
        if self.is_any_branch_parameter_symbolic():
            # finding the unique capacitances
            uniq_capacitances = []
            element_param = {"C": "EC", "JJ": "ECJ", "JJ2": "ECJ"}
            for c, b in enumerate(
                [
                    t
                    for t in self.branches
                    if t.type == "C" or t.type == "JJ" or t.type == "JJ2"
                ]
            ):
                if len(set(b.nodes)) > 1:  # check to see if branch is shorted
                    if b.parameters[element_param[b.type]] not in uniq_capacitances:
                        uniq_capacitances.append(b.parameters[element_param[b.type]])

            for index, var in enumerate(uniq_capacitances):
                L = L.subs(var, 1 / (8 * symbols(f"C{index + 1}")))
                L_old = L_old.subs(var, 1 / (8 * symbols(f"C{index + 1}")))
        return L, L_old

    # Serialize will not currently work for the Circuit class.
    @staticmethod
    def default_params() -> Dict[str, Any]:
        # return {"EJ": 15.0, "EC": 0.3, "ng": 0.0, "ncut": 30, "truncated_dim": 10}

        return {}

    @staticmethod
    def are_branchsets_disconnected(
        branch_list1: List[Branch], branch_list2: List[Branch]
    ) -> bool:
        """
        Determines whether two sets of branches are disconnected.

        Parameters
        ----------
        branch_list1:
            first list of branches
        branch_list2:
            second list of branches

        Returns
        -------
        bool
            Returns True if the branches have a connection, else False
        """
        node_array1 = np.array([branch.node_ids() for branch in branch_list1]).flatten()
        node_array2 = np.array([branch.node_ids() for branch in branch_list2]).flatten()
        return np.intersect1d(node_array1, node_array2).size == 0

    @staticmethod
    def _parse_nodes(branches_list) -> Tuple[Optional[Node], List[Node]]:
        node_index_list = []
        for branch_list_input in branches_list:
            for idx in [1, 2]:
                node_idx = branch_list_input[idx]
                if node_idx not in node_index_list:
                    node_index_list.append(node_idx)
        node_index_list.sort()
        ground_node = None
        if 0 in node_index_list:
            ground_node = Node(0, 0)
            node_index_list.remove(0)
        return ground_node, [Node(idx, 0) for idx in node_index_list]

    @staticmethod
    def _parse_branches(
        branches_list, nodes: List[Node], ground_node: Optional[Node]
    ) -> Tuple[List[Branch], Dict[Union[Any, Symbol], Union[Any, float]]]:

        branches = []
        branch_var_dict = {}  # dict stores init values of all vars from input string

        for branch_list_input in branches_list:

            branch_type = branch_list_input[0]
            node_id1, node_id2 = branch_list_input[1], branch_list_input[2]

            if (branch_type == "JJ" or branch_type == "JJ2") and len(
                branch_list_input
            ) != 5:
                raise Exception(
                    "Incorrect number of parameters: specification of JJ input in "
                    f"line: {branch_list_input}"
                )
            elif (branch_type == "L" or branch_type == "C") and len(
                branch_list_input
            ) != 4:
                raise Exception(
                    "Incorrect number of parameters: specification of C or L "
                    f"in line: {branch_list_input}"
                )

            branch_params, var_dict = parse_branch_parameters(
                branch_list_input[3:], branch_type
            )

            for var in var_dict:
                if var in branch_var_dict:
                    raise Exception(str(var) + " has already been initialized.")
                branch_var_dict[var] = var_dict[var]
            for param in [
                param for param in branch_params if not isinstance(param, float)
            ]:
                if param not in branch_var_dict.keys():
                    raise Exception(str(param) + " has not been initialized.")

            parameters = branch_params

            if node_id1 == 0:
                branches.append(
                    Branch(
                        ground_node,
                        nodes[node_id2 - 1],
                        branch_type,
                        parameters,
                        id_str=str(len(branches)),
                    )
                )
            elif node_id2 == 0:
                branches.append(
                    Branch(
                        nodes[node_id1 - 1],
                        ground_node,
                        branch_type,
                        parameters,
                        id_str=str(len(branches)),
                    )
                )
            else:
                branches.append(
                    Branch(
                        nodes[node_id1 - 1],
                        nodes[node_id2 - 1],
                        branch_type,
                        parameters,
                        id_str=str(len(branches)),
                    )
                )
        return branches, branch_var_dict

    @classmethod
    def from_yaml(
        cls,
        input_string: str,
        from_file: bool = True,
        basis_completion: str = "heuristic",
        is_flux_dynamic: bool = True,
        initiate_sym_calc: bool = True,
        identify_LC_variables: bool = True,
    ):
        """
        Constructs the instance of Circuit from an input string. Here is an example of
        an input string that is used to initiate an object of the
        class `SymbolicCircuit`:

            ```
            #zero-pi.yaml
            nodes    : 4
            # zero-pi
            branches:
            - [JJ, 1,2, EJ = 10, 20]
            - [JJ, 3,4, 10, 20]
            - [L, 2,3, 0.008]
            - [L, 4,1, 0.008]
            - [C, 1,3, 0.02]
            - [C, 2,4, 0.02]
            ```

        Parameters
        ----------
        input_string:
            String describing the number of nodes and branches connecting then along
            with their parameters
        from_file:
            Set to True by default, when a file name should be provided to
            `input_string`, else the circuit graph description in YAML should be
            provided as a string.
        basis_completion:
            choices: "heuristic" or "canonical"; used to choose a type of basis
            for completing the transformation matrix. Set to "heuristic" by default.
        is_flux_dynamic: bool
            set to False by default. Indicates if the flux allocation is done by
            assuming that flux is time dependent. When set to True, it disables the
            option to change the closure branches.
        initiate_sym_calc:
            set to True by default. Initiates the object attributes by calling
            the function `initiate_symboliccircuit` method when set to True.
            Set to False for debugging.
        identify_LC_variables:
            set to True by default. If set to True, the extended variables that only
            appears in the quadratic Hamiltonian is identified.

        Returns
        -------
            Instance of the class `SymbolicCircuit`
        """
        if from_file:
            file = open(input_string, "r")
            circuit_desc = file.read()
            file.close()
        else:
            circuit_desc = input_string

        input_dictionary = yaml.load(circuit_desc, Loader=yaml.FullLoader)

        ground_node, nodes = cls._parse_nodes(input_dictionary["branches"])

        branches, branch_var_dict = cls._parse_branches(
            input_dictionary["branches"], nodes, ground_node
        )

        circuit = cls(
            nodes,
            branches,
            ground_node=ground_node,
            is_flux_dynamic=is_flux_dynamic,
            branch_var_dict=branch_var_dict,
            basis_completion=basis_completion,
            initiate_sym_calc=initiate_sym_calc,
            input_string=circuit_desc,
            identify_LC_variables=identify_LC_variables,
        )

        return circuit

    def _independent_modes(
        self,
        branch_subset: List[Branch],
        single_nodes: bool = True,
        basisvec_entries: Optional[List[int]] = None,
    ):
        """
        Returns the vectors which span a subspace where there is no generalized flux
        difference across the branches present in the branch_subset.

        Parameters
        ----------
        single_nodes:
            if the single nodes are taken into consideration for basis vectors.
        """
        if basisvec_entries is None:
            basisvec_entries = [1, 0]

        nodes_copy = (
            self._node_list_without_ground.copy()
        )  # copying self.nodes as it is being modified

        if self.is_grounded:  # needed as ground node is not included in self.nodes
            nodes_copy.append(self.ground_node)

        for node in nodes_copy:  # reset the node markers
            node.marker = 0

        # step 2: finding the maximum connected set of independent branches in
        # branch_subset, then identifying the sets of nodes in each of those sets
        branch_subset_copy = branch_subset.copy()

        max_connected_subgraphs = []  # list containing the maximum connected subgraphs

        while (
            len(branch_subset_copy) > 0
        ):  # while loop ends when all the branches are sorted
            b_0 = branch_subset_copy.pop(0)
            max_connected_subgraph = [b_0]

            while not self.are_branchsets_disconnected(
                max_connected_subgraph, branch_subset_copy
            ):
                for b1 in branch_subset_copy:
                    for b2 in max_connected_subgraph:
                        if b1.is_connected(b2):
                            max_connected_subgraph.append(b1)
                            branch_subset_copy.remove(b1)
                            break
            max_connected_subgraphs.append(max_connected_subgraph)

        # finding the nodes in each of the maximum connected subgraph
        nodes_in_max_connected_branchsets = [
            list(set(sum([branch.nodes for branch in branch_set], ())))
            for branch_set in max_connected_subgraphs
        ]

        # using node.marker to mark the maximum connected subgraph to which a node
        # belongs
        for node_set_index, node_set in enumerate(nodes_in_max_connected_branchsets):
            for node in node_set:
                if any([n.is_ground() for n in node_set]):
                    node.marker = -1
                else:
                    node.marker = node_set_index + 1

        # marking ground nodes separately
        for node in nodes_copy:
            if node.is_ground():
                node.marker = -1

        node_branch_set_indices = [
            node.marker for node in nodes_copy
        ]  # identifies which node belongs to which maximum connected subgraphs;
        # different numbers on two nodes indicates that they are not connected through
        # any of the branches in branch_subset. 0 implies the node does not belong to
        # any of the branches in max connected branch subsets and -1 implies the max
        # connected branch set is connected to ground.

        # step 3: Finding the linearly independent vectors spanning the vector space
        # represented by branch_set_index
        basis = []

        unique_branch_set_markers = list(set(node_branch_set_indices))
        # removing the marker -1 as it is grounded.
        branch_set_markers_ungrounded = [
            marker for marker in unique_branch_set_markers if marker != -1
        ]

        for index in branch_set_markers_ungrounded:
            basis.append(
                [
                    basisvec_entries[0] if i == index else basisvec_entries[1]
                    for i in node_branch_set_indices
                ]
            )

        if single_nodes:  # taking the case where the node_branch_set_index is 0
            single_node_modes = []
            if node_branch_set_indices.count(0) > 0:
                ref_vector = [
                    basisvec_entries[0] if i == 0 else basisvec_entries[1]
                    for i in node_branch_set_indices
                ]
                positions = [
                    index
                    for index, num in enumerate(ref_vector)
                    if num == basisvec_entries[0]
                ]
                for pos in positions:
                    single_node_modes.append(
                        [
                            basisvec_entries[0] if x == pos else basisvec_entries[1]
                            for x, num in enumerate(node_branch_set_indices)
                        ]
                    )

            for mode in single_node_modes:
                mat = np.array(basis + [mode])
                if np.linalg.matrix_rank(mat) == len(mat):
                    basis.append(mode)

        if (
            self.is_grounded
        ):  # if grounded remove the last column and first row corresponding to the
            basis = [i[:-1] for i in basis]

        return basis

    @staticmethod
    def _mode_in_subspace(mode, subspace) -> bool:
        """
        Method to check if the vector mode is a part of the subspace provided as a set
        of vectors

        Parameters
        ----------
        mode:
            numpy ndarray of one dimension.
        subspace:
            numpy ndarray which represents a collection of basis vectors for a vector
            subspace
        """
        if len(subspace) == 0:
            return False
        matrix = np.vstack([subspace, np.array(mode)])
        return np.linalg.matrix_rank(matrix) == len(subspace)

    def check_transformation_matrix(
        self, transformation_matrix: ndarray, enable_warnings: bool = True
    ):
        """
        Method to identify the different modes in the transformation matrix provided by
        the user.

        Parameters
        ----------
        transformation_matrix:
            numpy ndarray which is a square matrix having the dimensions of the number
            of nodes present in the circuit.
        warnings:
            If False, will not raise the warnings regarding any unidentified modes. It
            is set to True by default.

        Returns
        -------
            A dictionary of lists which has the variable indices classified with
            var indices corresponding to the rows of the transformation matrix
        """
        # basic check to see if the matrix is invertible
        if np.linalg.det(transformation_matrix) == 0:
            raise Exception("The transformation matrix provided is not invertible.")

        # find all the different types of modes present in the circuit.

        # *************************** Finding the Periodic Modes **********************
        selected_branches = [branch for branch in self.branches if branch.type == "L"]
        periodic_modes = self._independent_modes(selected_branches)

        # *************************** Finding the frozen modes **********************
        selected_branches = [branch for branch in self.branches if branch.type != "L"]
        frozen_modes = self._independent_modes(selected_branches, single_nodes=True)

        # *************************** Finding the Cyclic Modes ****************
        selected_branches = [branch for branch in self.branches if branch.type != "C"]
        free_modes = self._independent_modes(selected_branches)

        # ***************************# Finding the LC Modes ****************
        selected_branches = [branch for branch in self.branches if branch.type == "JJ"]
        LC_modes = self._independent_modes(selected_branches, single_nodes=False)

        # ******************* including the Σ mode ****************
        Σ = [1] * len(self._node_list_without_ground)
        if not self.is_grounded:  # only append if the circuit is not grounded
            mat = np.array(frozen_modes + [Σ])
            # check to see if the vectors are still independent
            if np.linalg.matrix_rank(mat) < len(frozen_modes) + 1:
                frozen_modes = frozen_modes[1:] + [Σ]
            else:
                frozen_modes.append(Σ)

        # *********** Adding periodic, free and extended modes to frozen ************
        modes = []  # starting with the frozen modes

        for m in (
            frozen_modes + free_modes + periodic_modes + LC_modes  # + extended_modes
        ):  # This order is important
            if not self._mode_in_subspace(m, modes):
                modes.append(m)

        for m in LC_modes:  # adding the LC modes to the basis
            if not self._mode_in_subspace(m, modes):
                modes.append(m)

        var_categories_circuit: Dict[str, list] = {
            "periodic": [],
            "extended": [],
            "free": [],
            "frozen": [],
        }

        for x, mode in enumerate(modes):
            # calculate the number of periodic modes
            if self._mode_in_subspace(Σ, [mode]) and not self.is_grounded:
                continue

            if self._mode_in_subspace(mode, frozen_modes):
                var_categories_circuit["frozen"].append(x + 1)
                continue

            if self._mode_in_subspace(mode, free_modes):
                var_categories_circuit["free"].append(x + 1)
                continue

            if self._mode_in_subspace(mode, periodic_modes):
                var_categories_circuit["periodic"].append(x + 1)
                continue

            # Any mode which survived the above conditionals is an extended mode
            var_categories_circuit["extended"].append(x + 1)

        # Classifying the modes given in the transformation by the user

        user_given_modes = transformation_matrix.transpose()

        var_categories_user: Dict[str, list] = {
            "periodic": [],
            "extended": [],
            "free": [],
            "frozen": [],
        }

        for x, mode in enumerate(user_given_modes):
            # calculate the number of periodic modes
            if self._mode_in_subspace(Σ, [mode]) and not self.is_grounded:
                continue

            if self._mode_in_subspace(mode, frozen_modes):
                var_categories_user["frozen"].append(x + 1)
                continue

            if self._mode_in_subspace(mode, free_modes):
                var_categories_user["free"].append(x + 1)
                continue

            if self._mode_in_subspace(mode, periodic_modes):
                var_categories_user["periodic"].append(x + 1)
                continue

            # Any mode which survived the above conditionals is an extended mode
            var_categories_user["extended"].append(x + 1)

        # comparing the modes in the user defined and the code generated transformation

        mode_types = ["periodic", "extended", "free", "frozen"]

        for mode_type in mode_types:
            num_extra_modes = len(var_categories_circuit[mode_type]) - len(
                var_categories_user[mode_type]
            )
            if num_extra_modes > 0 and enable_warnings:
                warnings.warn(
                    "Number of extra "
                    + mode_type
                    + " modes found: "
                    + str(num_extra_modes)
                    + "\n"
                )

        return var_categories_user

    def variable_transformation_matrix(
        self, identify_LC_variables: bool = True
    ) -> Tuple[ndarray, Dict[str, List[int]]]:
        """
        Evaluates the boundary conditions and constructs the variable transformation
        matrix, which is returned along with the dictionary `var_categories` which
        classifies the types of variables present in the circuit.

        Parameters
        ----------
        identify_LC_variables:
            if True, the LC variables (variables that only appears in the quadratic
            part of the Hamiltonian) will be identified

        Returns
        -------
            tuple of transformation matrix for the node variables and `var_categories`
            dict which classifies the variable types for each variable index
        """

        # ****************  Finding the Periodic Modes ****************
        selected_branches = [branch for branch in self.branches if branch.type == "L"]
        periodic_modes = self._independent_modes(selected_branches)

        # ****************  Finding the frozen modes ****************
        selected_branches = [branch for branch in self.branches if branch.type != "L"]
        frozen_modes = self._independent_modes(selected_branches, single_nodes=True)

        # **************** Finding the Cyclic Modes ****************
        selected_branches = [branch for branch in self.branches if branch.type != "C"]
        free_modes = self._independent_modes(selected_branches)

        # ****************  including the Σ mode ****************
        Σ = [1] * len(self._node_list_without_ground)
        if not self.is_grounded:  # only append if the circuit is not grounded
            mat = np.array(frozen_modes + [Σ])
            # check to see if the vectors are still independent
            if np.linalg.matrix_rank(mat) < len(frozen_modes) + 1:
                frozen_modes = frozen_modes[1:] + [Σ]
            else:
                frozen_modes.append(Σ)

        # **************** Finding the LC Modes ****************
        if identify_LC_variables:
            selected_branches = [
                branch for branch in self.branches if branch.type == "JJ"
            ]
            LC_modes = self._independent_modes(
                selected_branches, single_nodes=False, basisvec_entries=[-1, 1]
            )

        # **************** Adding frozen, free, periodic , LC and extended modes ****
        modes = []  # starting with an empty list

        identified_modes = frozen_modes + free_modes + periodic_modes
        if identify_LC_variables:
            identified_modes += LC_modes
        for m in identified_modes:  # + extended_modes  # This order is important
            mat = np.array(modes + [m])
            if np.linalg.matrix_rank(mat) == len(mat):
                modes.append(m)

        # ********** Completing the Basis ****************
        # step 4: construct the new set of basis vectors

        # constructing a standard basis
        node_count = len(self._node_list_without_ground)
        standard_basis = [np.ones(node_count)]

        vector_ref = np.zeros(node_count)
        if node_count > 2:
            vector_ref[: node_count - 2] = 1
        else:
            vector_ref[: node_count - 1] = 1

        vector_set = list((itertools.permutations(vector_ref, node_count)))
        item = 0
        while np.linalg.matrix_rank(np.array(standard_basis)) < node_count:
            a = vector_set[item]
            item += 1
            mat = np.array(standard_basis + [a])
            if np.linalg.matrix_rank(mat) == len(mat):
                standard_basis = standard_basis + [list(a)]

        standard_basis = np.array(standard_basis)

        if self.basis_completion == "canonical":
            standard_basis = np.identity(len(self._node_list_without_ground))

        new_basis = modes.copy()

        for m in standard_basis:  # completing the basis
            mat = np.array([i for i in new_basis] + [m])
            if np.linalg.matrix_rank(mat) == len(mat):
                new_basis.append(m)

        new_basis = np.array(new_basis)

        # sorting the basis so that the free, periodic and frozen variables occur at
        # the beginning.
        if not self.is_grounded:
            pos_Σ = [i for i in range(len(new_basis)) if new_basis[i].tolist() == Σ]
        else:
            pos_Σ = []

        pos_free = [
            i
            for i in range(len(new_basis))
            if i not in pos_Σ
            if new_basis[i].tolist() in free_modes
        ]
        pos_periodic = [
            i
            for i in range(len(new_basis))
            if i not in pos_Σ
            if i not in pos_free
            if new_basis[i].tolist() in periodic_modes
        ]
        pos_frozen = [
            i
            for i in range(len(new_basis))
            if i not in pos_Σ
            if i not in pos_free
            if i not in pos_periodic
            if new_basis[i].tolist() in frozen_modes
        ]
        pos_rest = [
            i
            for i in range(len(new_basis))
            if i not in pos_Σ
            if i not in pos_free
            if i not in pos_periodic
            if i not in pos_frozen
        ]
        pos_list = pos_periodic + pos_rest + pos_free + pos_frozen + pos_Σ
        # transforming the new_basis matrix
        new_basis = new_basis[pos_list].T

        # saving the variable identification to a dict
        var_categories = {
            "periodic": [
                i + 1 for i in range(len(pos_list)) if pos_list[i] in pos_periodic
            ],
            "extended": [
                i + 1 for i in range(len(pos_list)) if pos_list[i] in pos_rest
            ],
            "free": [i + 1 for i in range(len(pos_list)) if pos_list[i] in pos_free],
            "frozen": [
                i + 1 for i in range(len(pos_list)) if pos_list[i] in pos_frozen
            ],
        }

        return np.array(new_basis), var_categories

    def update_param_init_val(self, param_name, value):
        """
        Updates the param init val for param_name
        """
        for index, param in enumerate(list(self.symbolic_params.keys())):
            if param_name == param.name:
                self.symbolic_params[param] = value
                break
        if self.is_purely_harmonic:
            (
                self.normal_mode_freqs,
                self.transformation_matrix,
            ) = self.purely_harmonic_transformation()
            self.configure()

    def _junction_terms(self):
        terms = 0
        # looping over all the junction terms
        junction_branches = [branch for branch in self.branches if branch.type == "JJ"]
        for jj_branch in junction_branches:
            # adding external flux
            phi_ext = 0
            if jj_branch in self.closure_branches:
                if not self.is_flux_dynamic:
                    index = self.closure_branches.index(jj_branch)
                    phi_ext += self.external_fluxes[index]
            if self.is_flux_dynamic:
                flux_branch_assignment = self._time_dependent_flux_distribution()
                phi_ext += flux_branch_assignment[int(jj_branch.id_str)]

            # if loop to check for the presence of ground node
            if jj_branch.nodes[1].index == 0:
                terms += -jj_branch.parameters["EJ"] * sympy.cos(
                    -symbols(f"φ{jj_branch.nodes[0].index}") + phi_ext
                )
            elif jj_branch.nodes[0].index == 0:
                terms += -jj_branch.parameters["EJ"] * sympy.cos(
                    symbols(f"φ{jj_branch.nodes[1].index}") + phi_ext
                )
            else:
                terms += -jj_branch.parameters["EJ"] * sympy.cos(
                    symbols(f"φ{jj_branch.nodes[1].index}")
                    - symbols(f"φ{jj_branch.nodes[0].index}")
                    + phi_ext
                )
        return terms

    def _JJ2_terms(self):
        terms = 0
        # looping over all the JJ2 branches
        for jj2_branch in [t for t in self.branches if t.type == "JJ2"]:
            # adding external flux
            phi_ext = 0
            if jj2_branch in self.closure_branches:
                if not self.is_flux_dynamic:
                    index = self.closure_branches.index(jj2_branch)
                    phi_ext += self.external_fluxes[index]
            if self.is_flux_dynamic:
                flux_branch_assignment = self._time_dependent_flux_distribution()
                phi_ext += flux_branch_assignment[int(jj2_branch.id_str)]

            # if loop to check for the presence of ground node
            if jj2_branch.nodes[1].index == 0:
                terms += -jj2_branch.parameters["EJ"] * sympy.cos(
                    2 * (-symbols(f"φ" + str(jj2_branch.nodes[0].index)) + phi_ext)
                )
            elif jj2_branch.nodes[0].index == 0:
                terms += -jj2_branch.parameters["EJ"] * sympy.cos(
                    2 * (symbols(f"φ{jj2_branch.nodes[1].index}") + phi_ext)
                )
            else:
                terms += -jj2_branch.parameters["EJ"] * sympy.cos(
                    2
                    * (
                        symbols(f"φ{jj2_branch.nodes[1].index}")
                        - symbols(f"φ{jj2_branch.nodes[0].index}")
                        + phi_ext
                    )
                )
        return terms

    def _inductance_matrix(self, substitute_params: bool = False):
        """
        Generate a inductance matrix for the circuit

        Parameters
        ----------
        substitute_params:
            when set to True all the symbolic branch parameters are substituted with
            their corresponding attributes in float, by default False

        Returns
        -------
        _type_
            _description_
        """
        branches_with_inductance = [
            branch for branch in self.branches if branch.type == "L"
        ]

        param_init_vals_dict = self.symbolic_params

        # filling the non-diagonal entries
        if not self.is_grounded:
            num_nodes = len(self._node_list_without_ground)
        else:
            num_nodes = len(self._node_list_without_ground) + 1
        if not self.is_any_branch_parameter_symbolic() or substitute_params:
            L_mat = np.zeros([num_nodes, num_nodes])
        else:
            L_mat = sympy.zeros(num_nodes)

        for branch in branches_with_inductance:
            if len(set(branch.nodes)) > 1:  # branch if shorted is not considered
                inductance = branch.parameters["EL"]
                if type(inductance) != float and substitute_params:
                    inductance = param_init_vals_dict[inductance]
                if self.is_grounded:
                    L_mat[branch.nodes[0].index, branch.nodes[1].index] += -inductance
                else:
                    L_mat[
                        branch.nodes[0].index - 1, branch.nodes[1].index - 1
                    ] += -inductance

        if not self.is_any_branch_parameter_symbolic() or substitute_params:
            L_mat = L_mat + L_mat.T - np.diag(L_mat.diagonal())
        else:
            L_mat = L_mat + L_mat.T - sympy.diag(*L_mat.diagonal())

        for i in range(L_mat.shape[0]):  # filling the diagonal entries
            L_mat[i, i] = -np.sum(L_mat[i, :])

        if self.is_grounded:  # if grounded remove the 0th column and row from L_mat
            L_mat = L_mat[1:, 1:]
        return L_mat

    def _capacitance_matrix(self, substitute_params: bool = False):
        """
        Generate a capacitance matrix for the circuit

        Parameters
        ----------
        substitute_params:
            when set to True all the symbolic branch parameters are substituted with
            their corresponding attributes in float, by default False

        Returns
        -------
        _type_
            _description_
        """
        branches_with_capacitance = [
            branch for branch in self.branches if branch.type in ["C", "JJ", "JJ2"]
        ]
        capacitance_param_for_branch_type = {
            "C": "EC",
            "JJ": "ECJ",
            "JJ2": "ECJ",
        }

        param_init_vals_dict = self.symbolic_params

        # filling the non-diagonal entries
        if not self.is_grounded:
            num_nodes = len(self._node_list_without_ground)
        else:
            num_nodes = len(self._node_list_without_ground) + 1
        if not self.is_any_branch_parameter_symbolic() or substitute_params:
            C_mat = np.zeros([num_nodes, num_nodes])
        else:
            C_mat = sympy.zeros(num_nodes)

        for branch in branches_with_capacitance:
            if len(set(branch.nodes)) > 1:  # branch if shorted is not considered
                capacitance = branch.parameters[
                    capacitance_param_for_branch_type[branch.type]
                ]
                if type(capacitance) != float and substitute_params:
                    capacitance = param_init_vals_dict[capacitance]
                if self.is_grounded:
                    C_mat[branch.nodes[0].index, branch.nodes[1].index] += -1 / (
                        capacitance * 8
                    )
                else:
                    C_mat[
                        branch.nodes[0].index - 1, branch.nodes[1].index - 1
                    ] += -1 / (capacitance * 8)

        if not self.is_any_branch_parameter_symbolic() or substitute_params:
            C_mat = C_mat + C_mat.T - np.diag(C_mat.diagonal())
        else:
            C_mat = C_mat + C_mat.T - sympy.diag(*C_mat.diagonal())

        for i in range(C_mat.shape[0]):  # filling the diagonal entries
            C_mat[i, i] = -np.sum(C_mat[i, :])

        if self.is_grounded:  # if grounded remove the 0th column and row from C_mat
            C_mat = C_mat[1:, 1:]
        return C_mat

    def _capacitor_terms(self):
        terms = 0
        branches_with_capacitance = [
            branch
            for branch in self.branches
            if branch.type == "C" or branch.type == "JJ" or branch.type == "JJ2"
        ]
        for c_branch in branches_with_capacitance:
            element_param = {"C": "EC", "JJ": "ECJ", "JJ2": "ECJ"}

            if c_branch.nodes[1].index == 0:
                terms += (
                    1
                    / (16 * c_branch.parameters[element_param[c_branch.type]])
                    * (symbols(f"vφ{c_branch.nodes[0].index}")) ** 2
                )
            elif c_branch.nodes[0].index == 0:
                terms += (
                    1
                    / (16 * c_branch.parameters[element_param[c_branch.type]])
                    * (-symbols(f"vφ{c_branch.nodes[1].index}")) ** 2
                )
            else:
                terms += (
                    1
                    / (16 * c_branch.parameters[element_param[c_branch.type]])
                    * (
                        symbols(f"vφ{c_branch.nodes[1].index}")
                        - symbols(f"vφ{c_branch.nodes[0].index}")
                    )
                    ** 2
                )
        return terms

    def _inductor_terms(self):
        terms = 0
        for l_branch in [branch for branch in self.branches if branch.type == "L"]:
            # adding external flux
            phi_ext = 0
            if l_branch in self.closure_branches:
                if not self.is_flux_dynamic:
                    index = self.closure_branches.index(l_branch)
                    phi_ext += self.external_fluxes[index]
            if self.is_flux_dynamic:
                flux_branch_assignment = self._time_dependent_flux_distribution()
                phi_ext += flux_branch_assignment[int(l_branch.id_str)]

            if l_branch.nodes[0].index == 0:
                terms += (
                    0.5
                    * l_branch.parameters["EL"]
                    * (symbols(f"φ{l_branch.nodes[1].index}") + phi_ext) ** 2
                )
            elif l_branch.nodes[1].index == 0:
                terms += (
                    0.5
                    * l_branch.parameters["EL"]
                    * (-symbols(f"φ{l_branch.nodes[0].index}") + phi_ext) ** 2
                )
            else:
                terms += (
                    0.5
                    * l_branch.parameters["EL"]
                    * (
                        symbols(f"φ{l_branch.nodes[1].index}")
                        - symbols(f"φ{l_branch.nodes[0].index}")
                        + phi_ext
                    )
                    ** 2
                )
        return terms

    def _spanning_tree(self):
        r"""
        Returns a spanning tree (as a list of branches) for the given instance. Notice that
        if the circuit contains multiple capacitive islands, the returned spanning tree will
        not include the capacitive twig between two capacitive islands.

        This function also returns all the branches that form superconducting loops, and a
        list of lists of nodes (node_sets), which keeps the generation info for nodes, e.g.,
        for the following spanning tree:

                   /---Node(2)
        Node(1)---'
                   '---Node(3)---Node(4)

        has the node_sets returned as [[Node(1)], [Node(2),Node(3)], [Node(4)]]

        Returns
        -------
            A spanning tree as a list of branches, which does not include capacitor branches,
            a list of branches that forms superconducting loops, and a list of lists of nodes
            (node_sets), which keeps the generation info for nodes of branches on the path.
        """

        # Make a copy of self; do not need symbolic expressions etc., so do a minimal
        # initialization only
        circ_copy = SymbolicCircuit.from_yaml(
            self.input_string, from_file=False, initiate_sym_calc=False
        )

        # **************** removing all the capacitive branches and updating the nodes *
        # identifying capacitive branches
        capacitor_branches = [
            branch for branch in list(circ_copy.branches) if branch.type == "C"
        ]
        for c_branch in capacitor_branches:
            for (
                node
            ) in (
                c_branch.nodes
            ):  # updating the branches attribute for each node that this branch
                # connects
                node.branches = [b for b in node.branches if b is not c_branch]
            circ_copy.branches.remove(c_branch)  # removing the branch

        num_float_nodes = 1
        while num_float_nodes > 0:  # breaks when no floating nodes are detected
            num_float_nodes = 0  # setting
            for node in circ_copy._node_list_without_ground:
                if len(node.branches) == 0:
                    circ_copy._node_list_without_ground.remove(node)
                    num_float_nodes += 1
                    continue
                if len(node.branches) == 1:
                    branches_connected_to_node = node.branches[0]
                    circ_copy.branches.remove(branches_connected_to_node)
                    for new_node in branches_connected_to_node.nodes:
                        if new_node != node:
                            new_node.branches = [
                                i
                                for i in new_node.branches
                                if i is not branches_connected_to_node
                            ]
                            num_float_nodes += 1
                            continue
                        else:
                            circ_copy._node_list_without_ground.remove(node)

        if circ_copy._node_list_without_ground == []:
            return [], [], []
        # *****************************************************************************

        # **************** Constructing the node_sets ***************
        if circ_copy.is_grounded:
            node_sets = [[circ_copy.ground_node]]
        else:
            node_sets = [
                [circ_copy._node_list_without_ground[0]]
            ]  # starting with the first set that has the first node as the only element

        num_nodes = len(circ_copy._node_list_without_ground)
        # this needs to be done as the ground node is not included in self.nodes
        if circ_copy.is_grounded:
            num_nodes += 1

        # finding all the sets of nodes and filling node_sets
        node_set_index = 0
        while (
            len(sum(node_sets, []))
            < num_nodes  # checking to see if all the nodes are present in node_sets
        ):
            node_set = []

            # code to handle two different capacitive islands in the circuit.
            if node_sets[node_set_index] == []:
                for node in circ_copy._node_list_without_ground:
                    if node not in flatten_list(node_sets):
                        node_sets[node_set_index].append(node)
                        break

            for node in node_sets[node_set_index]:
                node_set += node.connected_nodes("all")

            node_set = [
                x
                for x in list(set(node_set))
                if x not in flatten_list(node_sets[: node_set_index + 1])
            ]
            if node_set:
                node_set.sort(key=lambda node: node.index)

            node_sets.append(node_set)
            node_set_index += 1
        # ***************************

        # **************** constructing the spanning tree ##########
        tree_copy = []  # tree having branches of the instance that is copied

        def connecting_branches(n1: Node, n2: Node):
            return [branch for branch in n1.branches if branch in n2.branches]

        # find the branch connecting this node to another node in a previous node set.
        for index, node_set in enumerate(node_sets):
            if index == 0:
                continue
            for node in node_set:
                for prev_node in node_sets[index - 1]:
                    if len(connecting_branches(node, prev_node)) != 0:
                        tree_copy.append(connecting_branches(node, prev_node)[0])
                        break

        # ************* selecting the appropriate branches from circ as from circ_copy #
        def is_same_branch(branch_1: Branch, branch_2: Branch):
            return branch_1.id_str == branch_2.id_str

        tree = []  # tree having branches of the current instance
        for c_branch in tree_copy:
            tree += [b for b in self.branches if is_same_branch(b, c_branch)]

        # as the capacitors are removed to form the spanning tree, and as a result
        # floating branches as well, the set of all branches which form the
        # superconducting loops would be in circ_copy.
        superconducting_loop_branches = []
        for branch_copy in circ_copy.branches:
            superconducting_loop_branches += [
                branch
                for branch in self.branches
                if is_same_branch(branch, branch_copy)
            ]

        return tree, superconducting_loop_branches, node_sets

    def _closure_branches(self):
        r"""
        Returns and stores the closure branches in the circuit.
        """
        tree, superconducting_loop_branches, node_sets = self._spanning_tree()
        if tree == []:
            closure_branches = []
        else:
            closure_branches = [
                branch for branch in superconducting_loop_branches if branch not in tree
            ]
        return closure_branches

    def _time_dependent_flux_distribution(self):
        num_dynamical_variables = len(
            self.var_categories["periodic"] + self.var_categories["extended"]
        )

        # constructing the constraint matrix
        R = np.zeros([len(self.branches), len(self.closure_branches)])
        # constructing branch capacitance matrix
        C_diag = np.identity(len(self.branches)) * 0
        # constructing the matrix which transforms node to branch variables
        W = np.zeros([len(self.branches), len(self._node_list_without_ground)])

        for idx, closure_branch in enumerate(self.closure_branches):
            loop_branches = self._find_loop(closure_branch)
            for b_idx, branch in enumerate(loop_branches):
                R_elem = 1
                if branch.node_ids()[0] - branch.node_ids()[1] < 0:
                    R_elem = -1
                if (
                    b_idx > 0
                    and branch.node_ids()[0] != loop_branches[b_idx - 1].node_ids()[1]
                ):
                    R_elem *= -1
                R[self.branches.index(branch), idx] = R_elem

        for idx, branch in enumerate(self.branches):
            if branch.type in ["JJ", "C"]:
                EC = (
                    branch.parameters["EC"]
                    if branch.type == "C"
                    else branch.parameters["ECJ"]
                )
                if isinstance(EC, sympy.Expr):
                    EC = self.symbolic_params[EC]
                C_diag[idx, idx] = 1 / (EC * 8)
            for node_idx, node in enumerate(branch.nodes):
                if not node.is_ground():
                    n_id = self._node_list_without_ground.index(node)
                    W[idx, n_id] = 1 * (-1) ** node_idx

        M = np.vstack([(W.T @ C_diag), R.T])

        I = np.vstack(
            [
                np.zeros(
                    [len(self._node_list_without_ground), len(self.closure_branches)]
                ),
                np.identity(len(self.closure_branches)),
            ]
        )

        B = (np.linalg.pinv(M)) @ I
        return B.round(10) @ self.external_fluxes

    def _find_path_to_root(
        self, node: Node
    ) -> Tuple[int, List["Node"], List["Branch"]]:
        r"""
        Returns all the nodes and branches in the spanning tree path between the
        input node and the root of the spanning tree. Also returns the distance
        (generation) between the input node and the root node. The root of the spanning
        tree is node 0 if there is a physical ground node, otherwise it is node 1.

        Notice that the branches that sit on the boundaries of capacitive islands are
        not included in the branch list.

        Parameters
        ----------
        node: Node
            Node variable which is the input

        Returns
        -------
            An integer for the generation number, a list of ancestor nodes, and a list
            of branches on the path
        """
        # extract spanning tree node_sets (to determine the generation of the node)
        tree, superconducting_loop_branches, node_sets = self._spanning_tree()
        # find out the generation number of the node in the spanning tree
        # generation number begins from 0
        for igen, nodes in enumerate(node_sets):
            nodes_id = [node.index for node in nodes]
            if node.index in nodes_id:
                generation = igen
                break
        # find out the path from the node to the root
        current_node = node
        ancestor_nodes_list = []
        branch_path_to_root = []
        # looping over the parent generations
        for istep in range(generation - 1, -1, -1):
            # finding the parent of the current_node, and the branch that links the
            # parent and current_node
            for branch in tree:
                nodes_id = [node.index for node in node_sets[istep]]
                if (branch.nodes[1].index == current_node.index) and (
                    branch.nodes[0].index in nodes_id
                ):
                    ancestor_nodes_list.append(branch.nodes[0])
                    branch_path_to_root.append(branch)
                    current_node = branch.nodes[0]
                    break
                elif (branch.nodes[0].index == current_node.index) and (
                    branch.nodes[1].index in nodes_id
                ):
                    ancestor_nodes_list.append(branch.nodes[1])
                    branch_path_to_root.append(branch)
                    current_node = branch.nodes[1]
                    break
        ancestor_nodes_list.reverse()
        branch_path_to_root.reverse()
        return generation, ancestor_nodes_list, branch_path_to_root

    def _find_loop(self, closure_branch: Branch) -> List["Branch"]:
        r"""
        Find out the loop that is closed by the closure branch

        Parameters
        ----------
        closure_branch: Branch
            The input closure branch

        Returns
        -------
            A list of branches that corresponds to the loop closed by the closure branch
        """
        # find out ancestor nodes, path to root and generation number for each node in the
        # closure branch
        _, _, path_1 = self._find_path_to_root(closure_branch.nodes[0])
        _, _, path_2 = self._find_path_to_root(closure_branch.nodes[1])
        # find branches that are not common in the paths, and then add the closure branch to form the loop
        loop = (
            list(set(path_1) - set(path_2))
            + list(set(path_2) - set(path_1))
            + [closure_branch]
        )
        return self._order_branches_in_loop(loop)

    def _order_branches_in_loop(self, loop_branches):
        branches_in_order = [loop_branches[0]]
        branch_node_ids = [branch.node_ids() for branch in loop_branches]
        prev_node_id = branch_node_ids[0][0]
        while len(branches_in_order) < len(loop_branches):
            for branch in [
                brnch for brnch in loop_branches if brnch not in branches_in_order
            ]:
                if prev_node_id in branch.node_ids():
                    branches_in_order.append(branch)
                    break
            prev_node_id = [idx for idx in branch.node_ids() if idx != prev_node_id][0]
        return branches_in_order

    def _set_external_fluxes(self, closure_branches: List[Branch] = None):
        # setting the class properties

        if self.is_purely_harmonic:
            self.external_fluxes = []
            self.closure_branches = []
            return 0

        closure_branches = closure_branches or self._closure_branches()
        closure_branches = [branch for branch in closure_branches if branch.type != "C"]

        if len(closure_branches) > 0:
            self.closure_branches = closure_branches
            self.external_fluxes = [
                symbols("Φ" + str(i + 1)) for i in range(len(closure_branches))
            ]

    def _set_offset_charges(self):
        """
        Create the offset charge variables and store in class attribute offset_charges
        """
        self.offset_charges = []
        for p in self.var_categories["periodic"]:
            self.offset_charges = self.offset_charges + [symbols(f"ng{p}")]

    @staticmethod
    def round_symbolic_expr(expr: sympy.Expr, number_of_digits: int) -> sympy.Expr:
        rounded_expr = expr.expand()
        for term in sympy.preorder_traversal(expr.expand()):
            if isinstance(term, sympy.Float):
                rounded_expr = rounded_expr.subs(term, round(term, number_of_digits))
        return rounded_expr

    def generate_symbolic_lagrangian(
        self,
    ) -> Tuple[sympy.Expr, sympy.Expr, sympy.Expr, sympy.Expr]:
        r"""
        Returns four symbolic expressions: lagrangian_θ, potential_θ, lagrangian_φ,
        potential_φ, where θ represents the set of new variables and φ represents
        the set of node variables
        """
        transformation_matrix = self.transformation_matrix

        # defining the φ variables
        φ_dot_vars = [
            symbols(f"vφ{i}") for i in range(1, len(self._node_list_without_ground) + 1)
        ]

        # defining the θ variables
        θ_vars = [
            symbols(f"θ{i}") for i in range(1, len(self._node_list_without_ground) + 1)
        ]
        # defining the θ dot variables
        θ_dot_vars = [
            symbols(f"vθ{i}") for i in range(1, len(self._node_list_without_ground) + 1)
        ]
        # writing φ in terms of θ variables
        φ_vars_θ = transformation_matrix.dot(θ_vars)
        # writing φ dot vars in terms of θ variables
        φ_dot_vars_θ = transformation_matrix.dot(θ_dot_vars)

        # C_terms = self._C_terms()
        C_mat = self._capacitance_matrix()
        if not self.is_any_branch_parameter_symbolic():
            # in terms of node variables
            C_terms_φ = C_mat.dot(φ_dot_vars).dot(φ_dot_vars) * 0.5
            # in terms of new variables
            C_terms_θ = C_mat.dot(φ_dot_vars_θ).dot(φ_dot_vars_θ) * 0.5
        else:
            C_terms_φ = (sympy.Matrix(φ_dot_vars).T * C_mat * sympy.Matrix(φ_dot_vars))[
                0
            ] * 0.5  # in terms of node variables
            C_terms_θ = (
                sympy.Matrix(φ_dot_vars_θ).T * C_mat * sympy.Matrix(φ_dot_vars_θ)
            )[
                0
            ] * 0.5  # in terms of new variables

        inductor_terms_φ = self._inductor_terms()

        JJ_terms_φ = self._junction_terms() + self._JJ2_terms()

        lagrangian_φ = C_terms_φ - inductor_terms_φ - JJ_terms_φ

        potential_φ = inductor_terms_φ + JJ_terms_φ
        potential_θ = (
            potential_φ.copy()
        )  # copying the potential in terms of the old variables to make substitutions

        for index in range(
            len(self._node_list_without_ground)
        ):  # converting potential to new variables
            potential_θ = potential_θ.subs(symbols(f"φ{index + 1}"), φ_vars_θ[index])

        # eliminating the frozen variables
        for frozen_var_index in self.var_categories["frozen"]:
            sub = sympy.solve(
                potential_θ.diff(symbols(f"θ{frozen_var_index}")),
                symbols(f"θ{frozen_var_index}"),
            )
            potential_θ = potential_θ.replace(symbols(f"θ{frozen_var_index}"), sub[0])

        lagrangian_θ = C_terms_θ - potential_θ

        return lagrangian_θ, potential_θ, lagrangian_φ, potential_φ

    def generate_symbolic_hamiltonian(self, substitute_params=False) -> sympy.Expr:
        r"""
        Returns the Hamiltonian of the circuit in terms of the new variables
        :math:`\theta_i`.

        Parameters
        ----------
        substitute_params:
            When set to True, the symbols defined for branch parameters will be
            substituted with the numerical values in the respective Circuit attributes.
        """

        transformation_matrix = self.transformation_matrix

        # Excluding the frozen modes based on how they are organized in the method
        # variable_transformation_matrix
        if self.is_grounded:
            num_frozen_modes = len(self.var_categories["frozen"])
        else:
            num_frozen_modes = len(self.var_categories["frozen"]) + 1
        num_nodes = len(self._node_list_without_ground)

        # generating the C_mat_θ by inverting the capacitance matrix
        if self.is_any_branch_parameter_symbolic() and not substitute_params:
            C_mat_θ = (
                transformation_matrix.T
                * self._capacitance_matrix()
                * transformation_matrix
            )[
                0 : num_nodes - num_frozen_modes,
                0 : num_nodes - num_frozen_modes,
            ].inv()  # excluding the frozen modes
        else:
            C_mat_θ = np.linalg.inv(
                (
                    transformation_matrix.T
                    @ self._capacitance_matrix(substitute_params=substitute_params)
                    @ transformation_matrix
                )[
                    0 : num_nodes - num_frozen_modes,
                    0 : num_nodes - num_frozen_modes,
                ]
            )  # excluding the frozen modes

        p_θ_vars = [
            symbols(f"Q{i}") if i not in self.var_categories["free"]
            # replacing the free charge with 0, as it would not affect the circuit
            # Lagrangian.
            else 0
            for i in range(
                1, len(self._node_list_without_ground) + 1 - num_frozen_modes
            )
        ]  # defining the momentum variables

        # generating the kinetic energy terms for the Hamiltonian
        if not self.is_any_branch_parameter_symbolic():
            C_terms_new = (
                C_mat_θ.dot(p_θ_vars).dot(p_θ_vars) * 0.5
            )  # in terms of new variables
        else:
            C_terms_new = (sympy.Matrix(p_θ_vars).T * C_mat_θ * sympy.Matrix(p_θ_vars))[
                0
            ] * 0.5  # in terms of new variables

        hamiltonian_symbolic = C_terms_new + self.potential_symbolic

        # adding the offset charge variables
        for var_index in self.var_categories["periodic"]:
            hamiltonian_symbolic = hamiltonian_symbolic.subs(
                symbols(f"Q{var_index}"),
                symbols(f"n{var_index}") + symbols(f"ng{var_index}"),
            )
        # rounding the decimals
        return hamiltonian_symbolic.expand()

    def orthogonalize_island_vectors(
        self,
    ) -> Tuple[ndarray, Dict[str, Union[list, None]]]:
        """
        Based on the existing transformation matrix and variable categories, orthogonalize
        vector entries for frozen, periodic and free (cyclic) variables, and return the
        resulting transformation matrix.

        Returns
        -------
        A transformation matrix with orthogonalized frozen, periodic and cyclic variables,
        and a dictionary that shows the partition of nodes for each type of island.
        """
        # work with column vectors
        transformation_matrix_T = self.transformation_matrix.T
        orthogonalized_transformation_matrix_T = copy.deepcopy(transformation_matrix_T)
        island_vars_types = ["free", "frozen", "periodic"]
        island_vars_dict = {}
        # for each island type, test iteratively if there is any linearly-dependent pair of
        # vectors
        for island_type in island_vars_types:
            vars = self.var_categories[island_type]
            # linear depencency is only tested if there are more than 1 variables
            if len(vars) > 1:
                # collect the corresponding vectors for the given variable category
                vectors = [transformation_matrix_T[var_index - 1] for var_index in vars]
                # order these vectors in descending order of number of 1
                vectors.sort(key=lambda vector: np.count_nonzero(vector == 1.0))
                vectors = np.array(vectors)
                # for each vector, test if they are orthogonal with all the previous vectors
                # if so, do nothing, if not, orthogonalize
                for vec_index_i in range(1, len(vectors)):
                    for vec_index_j in reversed(range(vec_index_i)):
                        if vectors[vec_index_i] @ vectors[vec_index_j] != 0:
                            vectors[vec_index_i] -= vectors[vec_index_j]
                # replace the old vectors by the orthogonalized vectors
                for index, var_index in enumerate(vars):
                    orthogonalized_transformation_matrix_T[var_index - 1] = vectors[
                        index
                    ]
            # build a dictionary that shows the partition of node variables for each type of
            # island
            island_vars_dict[island_type] = [
                [
                    idx + 1
                    for idx, value in enumerate(
                        orthogonalized_transformation_matrix_T[var_index - 1]
                    )
                    if value == 1
                ]
                for var_index in vars
            ]
            # add the complement list of nodes as the first item of the island list
            # the first island is always either associated with the sigma variable, or not
            # associated with any variable at all
            if len(vars) > 0:
                complement_node_set = set([node.index for node in self.nodes])
                for node_list in island_vars_dict[island_type]:
                    complement_node_set -= set(node_list)
                complement_node_list = list(complement_node_set)
                island_vars_dict[island_type] = [
                    complement_node_list
                ] + island_vars_dict[island_type]
        return orthogonalized_transformation_matrix_T.T, island_vars_dict

    def junction_node_pairs(self) -> List[List[int]]:
        """
        Returns all the node pairs that are connected by at least one JJ.

        Returns
        -------
            A list of node pairs that are connected by at least one JJ.
        """
        JJ_branches = [branch for branch in self.branches if branch.type == "JJ"]
        JJ_node_pair_sets = [
            set([JJ_branch.nodes[0].index, JJ_branch.nodes[1].index])
            for JJ_branch in JJ_branches
        ]
        return [
            list(node_pair_set)
            for idx, node_pair_set in enumerate(JJ_node_pair_sets)
            if node_pair_set not in JJ_node_pair_sets[:idx]
        ]

    # TODO this function is very obsolete and consider removing it or turn it to
    # something useful
    def variable_transformation_transmon_fluxonium(
        self,
        transmon_var: List[List[int]] = [],
        fluxonium_var: List[List[int]] = [],
    ) -> ndarray:
        # extended and periodic variable offset
        periodic_var_offset = (
            len(self.var_categories["frozen"]) + len(self.var_categories["free"]) + 1
        )
        extended_var_offset = periodic_var_offset + len(self.var_categories["periodic"])
        # copy the orthogonalized transformation matrix
        modified_transformation_matrix_T = copy.deepcopy(
            self.orthogonalized_transformation_matrix.T
        )
        node_entries_to_be_completed = [node.index for node in self.nodes]
        if self.is_grounded:
            node_entries_to_be_completed.remove(0)
        # verify if the number of transmon and fluxonium variables are valid
        if len(transmon_var) > len(self.var_categories["periodic"]):
            raise Exception(
                "The number of transmon variables cannot be greater than "
                "the number of periodic variables."
            )
        if len(fluxonium_var) > len(self.var_categories["extended"]):
            raise Exception(
                "The number of fluxonium variables cannot be greater than "
                "the number of extended variables."
            )
        # for every transmon variable node pair, identify the island IDs
        periodic_island_node_list = self.island_node_dict["periodic"]
        if len(transmon_var) > 0:
            transmon_var_island_list = []
            for node_pair in transmon_var:
                # find out the island IDs for each node
                node_pair_island_IDs = []
                for island_ID, island_node_list in enumerate(periodic_island_node_list):
                    if (node_pair[0] in island_node_list) or (
                        node_pair[1] in island_node_list
                    ):
                        node_pair_island_IDs.append(island_ID)
                transmon_var_island_list.append(node_pair_island_IDs)
            # total number of periodic islands
            periodic_island_number = len(self.var_categories["periodic"]) + 1
            # require number of different islands > number of JJ node pairs provided
            if len(
                set.union(
                    *[
                        set(transmon_var_island)
                        for transmon_var_island in transmon_var_island_list
                    ]
                )
            ) <= len(transmon_var):
                raise Exception(
                    "For N node pairs provided for generating transmon variables, "
                    "these nodes must be in at least N+1 different islands."
                )
            # algorithm:
            # Loop over all the superconducting islands, for island i, whenever user
            # provided a node pair with islands [i,j], j < i, add the column corresponding
            # to the jth island to that of the ith island; if j = 0, do nothing.
            for island_ID in range(1, len(self.var_categories["periodic"]) + 1):
                # loop over all transmon_var_island
                for transmon_var_island in transmon_var_island_list:
                    if (
                        (transmon_var_island[0] == island_ID)
                        and (transmon_var_island[1] < island_ID)
                        and (transmon_var_island[1] != 0)
                    ):
                        modified_transformation_matrix_T[
                            periodic_var_offset + transmon_var_island[0] - 2
                        ] += modified_transformation_matrix_T[
                            periodic_var_offset + transmon_var_island[1] - 2
                        ]
                    elif (
                        (transmon_var_island[1] == island_ID)
                        and (transmon_var_island[0] < island_ID)
                        and (transmon_var_island[0] != 0)
                    ):
                        modified_transformation_matrix_T[
                            periodic_var_offset + transmon_var_island[1] - 2
                        ] += modified_transformation_matrix_T[
                            periodic_var_offset + transmon_var_island[0] - 2
                        ]
            # to create a list of node entries to be completed, the algorithm is:
            # for every transmon variable node pairs, try remove the first node, if not
            # exist, remove the second
            # if there is a node pair that contains a ground node, then the row entry for
            # other node can be automatically completed
            for transmon_node_pair in transmon_var:
                try:
                    node_entries_to_be_completed.remove(transmon_node_pair[0])
                except ValueError:
                    node_entries_to_be_completed.remove(transmon_node_pair[1])

        elif len(fluxonium_var) > 0:
            # require number of different nodes > number of JJ node pairs provided
            if (
                len(
                    set.union(
                        *[
                            set(fluxonium_node_pair)
                            for fluxonium_node_pair in fluxonium_var
                        ]
                    )
                )
                < len(fluxonium_var) + 1
            ):
                raise Exception(
                    "For N node pairs provided for generating fluxonium variables, "
                    "at least N+1 different nodes need to be involved."
                )
            # eliminate the node entries that need not be filled in
            # if there is a node pair that contains a ground node, then the row entry for
            # other node can be automatically completed
            for fluxonium_node_pair in fluxonium_var:
                try:
                    node_entries_to_be_completed.remove(fluxonium_node_pair[0])
                except ValueError:
                    node_entries_to_be_completed.remove(fluxonium_node_pair[1])

        # fill in the missing entries
        # for fluxonium node pairs, each dictates one column entry for an extended variable
        # the entry for the first node is 1, 0 for the rest, except for node pairs that
        # contain a physical ground node, where only the entry for the ungrounded node is
        # set to 1
        if len(fluxonium_var) > 0:
            # generate a list that counts nodes that are used to generate fluxonium variables
            fluxonium_node = []
            for fluxonium_ID, fluxonium_node_pair in enumerate(fluxonium_var):
                modified_transformation_matrix_T[
                    fluxonium_ID + extended_var_offset - 1
                ] = np.zeros(np.shape(modified_transformation_matrix_T)[1])
                if (fluxonium_node_pair[0] != 0) and (
                    fluxonium_node_pair[0] not in fluxonium_node
                ):
                    modified_transformation_matrix_T[
                        fluxonium_ID + extended_var_offset - 1,
                        fluxonium_node_pair[0] - 1,
                    ] = 1.0
                    fluxonium_node.append(fluxonium_node_pair[0])
                else:
                    modified_transformation_matrix_T[
                        fluxonium_ID + extended_var_offset - 1,
                        fluxonium_node_pair[1] - 1,
                    ] = 1.0
                    fluxonium_node.append(fluxonium_node_pair[1])
        # generate a filling matrix, which consists of entries in the transformation matrix
        # that are to be filled in
        filling_matrix = np.zeros(
            (
                len(self.var_categories["extended"]) - len(fluxonium_var),
                np.shape(modified_transformation_matrix_T)[1]
                - len(transmon_var)
                - len(fluxonium_var),
            )
        )
        for column_number in range(np.shape(filling_matrix)[0]):
            filling_matrix[column_number][column_number] = 1.0
        # maps the filling matrix back to the empty entires of the original transformation matrix
        for column_number in range(np.shape(filling_matrix)[0]):
            for row_number in range(np.shape(filling_matrix)[1]):
                modified_transformation_matrix_T[
                    extended_var_offset + len(fluxonium_var) + column_number - 1
                ][node_entries_to_be_completed[row_number] - 1] = filling_matrix[
                    column_number
                ][
                    row_number
                ]
        # copy paste entries based on the fluxonium and transmon node pairs
        completed_node_entries = copy.deepcopy(node_entries_to_be_completed)
        entries_to_be_copied = transmon_var + fluxonium_var

        while len(entries_to_be_copied) > 0:
            # find the first entries to be copied that has an element in the completed
            # node entries
            for entry in entries_to_be_copied:
                # if there is any transmon or fluxonium node pair that connects to the physical ground
                # node, fill the corresponding node entries with zeros
                if entry[0] == 0:
                    modified_transformation_matrix_T[
                        extended_var_offset
                        + len(fluxonium_var)
                        - 1 : np.shape(modified_transformation_matrix_T)[0]
                        - 1
                        + self.is_grounded,
                        entry[1] - 1,
                    ] = np.zeros(
                        len(self.var_categories["extended"]) - len(fluxonium_var)
                    )
                    # update completed node entries and entries to be copied
                    entries_to_be_copied.remove(entry)
                elif entry[1] == 0:
                    modified_transformation_matrix_T[
                        extended_var_offset
                        + len(fluxonium_var)
                        - 1 : np.shape(modified_transformation_matrix_T)[0]
                        - 1
                        + self.is_grounded,
                        entry[0] - 1,
                    ] = np.zeros(
                        len(self.var_categories["extended"]) - len(fluxonium_var)
                    )
                    # update completed node entries and entries to be copied
                    entries_to_be_copied.remove(entry)
                elif (entry[0] in completed_node_entries) or (
                    entry[1] in completed_node_entries
                ):
                    if entry[0] in completed_node_entries:
                        # update completed node entries and entries to be copied
                        completed_node_entries.append(entry[1])
                        entries_to_be_copied.remove(entry)
                        # perform the copy
                        modified_transformation_matrix_T[
                            extended_var_offset + len(fluxonium_var) - 1 :, entry[1] - 1
                        ] = modified_transformation_matrix_T[
                            extended_var_offset + len(fluxonium_var) - 1 :, entry[0] - 1
                        ]
                        break
                    else:
                        # update completed node entries and entries to be copied
                        completed_node_entries.append(entry[0])
                        entries_to_be_copied.remove(entry)
                        # perform the copy
                        modified_transformation_matrix_T[
                            extended_var_offset + len(fluxonium_var) - 1 :, entry[0] - 1
                        ] = modified_transformation_matrix_T[
                            extended_var_offset + len(fluxonium_var) - 1 :, entry[1] - 1
                        ]
                        break
        return modified_transformation_matrix_T.T
