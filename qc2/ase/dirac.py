"""This module defines an ASE interface to DIRAC23.

Official website:
https://www.diracprogram.org/

GitLab repo:
https://gitlab.com/dirac/dirac
"""

import subprocess
import os
from typing import Optional, List, Dict, Tuple, Union
import warnings
import h5py
import numpy as np

from ase import Atoms
from ase.calculators.calculator import FileIOCalculator
from ase.calculators.calculator import InputError, CalculationFailed
from ase.units import Bohr
from ase.io import write
from .dirac_io import write_dirac_in, read_dirac_out, _update_dict
from .qc2_ase_base_class import BaseQc2ASECalculator


class DIRAC(FileIOCalculator, BaseQc2ASECalculator):
    """A general ASE calculator for the relativistic qchem DIRAC code.

    Args:
        FileIOCalculator (FileIOCalculator): Base class for calculators
            that write/read input/output files.
        BaseQc2ASECalculator (BaseQc2ASECalculator): Base class for
            ase calculartors in qc2.

    Example of a typical ASE-DIRAC input:

    >>> from ase import Atoms
    >>> from ase.build import molecule
    >>> from qc2.ase.dirac import DIRAC
    >>>
    >>> molecule = Atoms(...) or molecule = molecule('...')
    >>> molecule.calc = DIRAC(dirac={}, wave_function={}...)
    >>> energy = molecule.get_potential_energy()
    """
    implemented_properties: List[str] = ['energy']
    label: str = 'dirac'
    command: str = 'pam --inp=PREFIX.inp --mol=PREFIX.xyz --silent ' \
        '--get="AOMOMAT MRCONEE MDCINT"'

    def __init__(self,
                 restart: Optional[bool] = None,
                 ignore_bad_restart_file:
                 Optional[bool] = FileIOCalculator._deprecated,
                 label: Optional[str] = None,
                 atoms: Optional[Atoms] = None,
                 command: Optional[str] = None,
                 **kwargs) -> None:
        """ASE-DIRAC Class Constructor to initialize the object.

        Args:
            restart (bool, optional): Prefix for restart file.
                May contain a directory. Defaults to None: don't restart.
            ignore_bad_restart (bool, optional): Deprecated and will
                stop working in the future. Defaults to False.
            label (str, optional): Calculator name. Defaults to 'dirac'.
            atoms (Atoms, optional): Atoms object to which the calculator
                will be attached. When restarting, atoms will get its
                positions and unit-cell updated from file. Defaults to None.
            command (str, optional): Command used to start calculation.
                Defaults to None.
            directory (str, optional): Working directory in which
                to perform calculations. Defaults to '.'.
        """
        # initialize ASE base class Calculator.
        # see ase/ase/calculators/calculator.py.
        FileIOCalculator.__init__(self, restart, ignore_bad_restart_file,
                                  label, atoms, command, **kwargs)

        self.prefix = self.label

        # Check self.parameters input keys and values
        self.check_dirac_attributes()

        # initialize qc2 base class for ASE calculators.
        # see .qc2_ase_base_class.py
        BaseQc2ASECalculator.__init__(self)

    def check_dirac_attributes(self) -> None:
        """Checks for any missing and/or mispelling DIRAC input attribute.

        Notes:
            it can also be used to eventually set specific
            options in the near future.
        """
        recognized_key_sections: List[str] = [
            'dirac', 'general', 'molecule',
            'hamiltonian', 'wave_function', 'analyse', 'properties',
            'visual', 'integrals', 'grid', 'moltra'
            ]

        # check any mispelling
        for key, value in self.parameters.items():
            if key not in recognized_key_sections:
                raise InputError('Keyword', key,
                                 ' not recognized. Please check input.')

        # set default parameters
        if 'dirac' not in self.parameters:
            key = 'dirac'
            value = {'.title': 'DIRAC-ASE calculation',
                     '.wave function': ''}
            # **DIRAC heading must always come first in the dict/input
            self.parameters = _update_dict(self.parameters, key, value)

        if 'hamiltonian' not in self.parameters:
            self.parameters.update(hamiltonian={'.levy-leblond': ''})

        if 'wave_function' not in self.parameters:
            self.parameters.update(wave_function={'.scf': ''})

        if 'molecule' not in self.parameters:
            self.parameters.update(molecule={'*basis': {'.default': 'sto-3g'}})

        if 'integrals' not in self.parameters:
            # useful to compare with nonrel calc done with other programs
            self.parameters.update(integrals={'.nucmod': '1'})

        if '*charge' not in self.parameters['molecule']:
            self.parameters['molecule']['*charge'] = {'.charge': '0'}

        if '.4index' not in self.parameters['dirac']:
            # activates the transformation of integrals to MO basis
            self.parameters['dirac'].update({'.4index': ''})

        if ('.4index' in self.parameters['dirac'] and
                'moltra' not in self.parameters):
            # calculates all integrals, including core
            self.parameters.update(moltra={'.active': 'all'})

    def calculate(self, *args, **kwargs) -> None:
        """Execute DIRAC workflow."""
        FileIOCalculator.calculate(self, *args, **kwargs)

    def write_input(
            self,
            atoms: Optional[Atoms] = None,
            properties: Optional[List[str]] = None,
            system_changes: Optional[List[str]] = None
            ) -> None:
        """Generate all necessary inputs for DIRAC."""
        FileIOCalculator.write_input(self, atoms, properties, system_changes)

        # generate xyz geometry file
        xyz_file = self.prefix + ".xyz"
        write(xyz_file, atoms)

        # generate DIRAC inp file
        inp_file = self.prefix + ".inp"
        write_dirac_in(inp_file, **self.parameters)

    def read_results(self):
        """Read energy from DIRAC output file."""
        out_file = self.prefix + "_" + self.prefix + ".out"
        output = read_dirac_out(out_file)
        self.results = output

    def save(self, hdf5_filename: str) -> None:
        """Dumps electronic structure data to a HDF5 file.

        Args:
            hdf5_filename (str): HDF5 file to save the data to.

        Notes:
            HDF5 files are written following the QCSchema.

        Returns:
            None

        Example:
        >>> from ase.build import molecule
        >>> from qc2.ase.dirac import DIRAC
        >>>
        >>> molecule = molecule('H2')
        >>> molecule.calc = DIRAC()  # => RHF/STO-3G
        >>> molecule.calc.get_potential_energy()
        >>> molecule.calc.save('h2.h5')
        """
        # calculate 1- and 2-electron integrals in MO basis
        integrals = self.get_integrals()
        e_core = integrals[0]
        one_body_integrals = integrals[2]
        two_body_integrals = integrals[3]

        # open the HDF5 file in write mode
        file = h5py.File(hdf5_filename, "w")

        # set up general definitions for the QCSchema
        # 1 => general initial attributes
        schema_name = "qcschema_molecule"
        version = '1.dev'
        driver = "energy"
        energy = self._get_from_dirac_hdf5_file(
           '/result/wavefunctions/scf/energy')[0]
        # final status of the calculation
        status = self._get_from_dirac_hdf5_file(
            '/result/execution/status')[0]
        if status != 2:
            success = False
        else:
            success = True

        file.attrs['driver'] = driver
        file.attrs['schema_name'] = schema_name
        file.attrs['schema_version'] = version
        file.attrs['return_result'] = energy
        file.attrs['success'] = success

        # 2 => molecule group
        symbols = self._get_from_dirac_hdf5_file(
           '/input/molecule/symbols'
           )
        geometry = self._get_from_dirac_hdf5_file(
           '/input/molecule/geometry') / Bohr  # => in a.u
        molecular_charge = int(
            self.parameters['molecule']['*charge']['.charge'])
        atomic_numbers = self._get_from_dirac_hdf5_file(
            '/input/molecule/nuc_charge')
        # include here multiplicity ?
        # Not a good quantum number for relativistic DIRAC?
        molecular_multiplicity = ''

        molecule = file.require_group("molecule")
        molecule.attrs['symbols'] = symbols
        molecule.attrs['geometry'] = geometry
        molecule.attrs['molecular_charge'] = molecular_charge
        molecule.attrs['molecular_multiplicity'] = molecular_multiplicity
        molecule.attrs['atomic_numbers'] = atomic_numbers
        molecule.attrs['schema_name'] = "qcschema_molecule"
        molecule.attrs['schema_version'] = version

        # 3 => properties group
        calcinfo_nbasis = self._get_from_dirac_hdf5_file(
            '/input/aobasis/1/n_ao')[0]
        # of molecular orbitals
        nmo = self._get_from_dirac_hdf5_file(
           '/result/wavefunctions/scf/mobasis/n_mo')
        nmo = sum(nmo)
        # in case of relativistic calculations...
        if ('.nonrel' not in self.parameters['hamiltonian'] and
                '.levy-leblond' not in self.parameters['hamiltonian']):
            nmo = nmo // 2
            warnings.warn('At the moment, DIRAC-ASE relativistic calculations'
                          ' may not work properly with'
                          ' Qiskit and/or Pennylane...')
        # approximate definition of # of alpha and beta electrons
        # does not work for pure triplet ground states!?
        nuc_charge = self._get_from_dirac_hdf5_file(
            '/input/molecule/nuc_charge')
        nelec = int(sum(nuc_charge)) - molecular_charge
        calcinfo_nbeta = nelec // 2
        calcinfo_nalpha = nelec - calcinfo_nbeta
        calcinfo_natom = self._get_from_dirac_hdf5_file(
           '/input/molecule/n_atoms')[0]
        #warnings.warn('VQEs with triplet ground state molecules '
        #              'not supported by DIRAC-ASE...')

        properties = file.require_group("properties")
        properties.attrs['calcinfo_nbasis'] = calcinfo_nbasis
        properties.attrs['calcinfo_nmo'] = nmo
        properties.attrs['calcinfo_nalpha'] = calcinfo_nalpha
        properties.attrs['calcinfo_nbeta'] = calcinfo_nbeta
        properties.attrs['calcinfo_natom'] = calcinfo_natom
        properties.attrs['nuclear_repulsion_energy'] = e_core
        properties.attrs['return_energy'] = energy

        # 4 => model group
        # dealing with different types of basis
        if '.default' in self.parameters['molecule']['*basis']:
            basis = self.parameters['molecule']['*basis']['.default']
        else:
            basis = 'special'
        # electronic structure method used
        method = list(self.parameters['wave_function'].keys())[-1].strip('.')

        model = file.require_group("model")
        model.attrs['basis'] = basis
        model.attrs['method'] = method

        # 5 => provenance group
        provenance = file.require_group("provenance")
        provenance.attrs['creator'] = self.name
        provenance.attrs['version'] = version
        provenance.attrs['routine'] = f"ASE-{self.__class__.__name__}.save()"

        # 6 => keywords group
        file.require_group("keywords")

        # 7 => wavefunction group
        # tolerance to consider number zero.
        EQ_TOLERANCE = 1e-8

        # slipt 1-body integrals into alpha and beta contributions
        one_body_coefficients_a = np.zeros((nmo, nmo), dtype=np.float64)
        one_body_coefficients_b = np.zeros((nmo, nmo), dtype=np.float64)

        # transform alpha and beta 1-body coeffs into QCSchema format
        for p in range(nmo):
            for q in range(nmo):

                # alpha indexes
                alpha_p = 2 * p + 1
                alpha_q = 2 * q + 1

                # beta indexes
                beta_p = 2 * p + 2
                beta_q = 2 * q + 2

                # alpha and beta 1-body coeffs
                one_body_coefficients_a[p, q] = one_body_integrals[
                    (alpha_p, alpha_q)]
                one_body_coefficients_b[p, q] = one_body_integrals[
                    (beta_p, beta_q)]

        # truncate numbers lower than EQ_TOLERANCE
        one_body_coefficients_a[np.abs(
            one_body_coefficients_a) < EQ_TOLERANCE] = 0.
        one_body_coefficients_b[np.abs(
            one_body_coefficients_b) < EQ_TOLERANCE] = 0.

        # slipt 2-body coeffs into alpha-alpha, beta-beta,
        # alpha-beta and beta-alpha contributions
        two_body_coefficients_aa = np.zeros(
            (nmo, nmo, nmo, nmo), dtype=np.float64)
        two_body_coefficients_bb = np.zeros(
            (nmo, nmo, nmo, nmo), dtype=np.float64)
        two_body_coefficients_ab = np.zeros(
            (nmo, nmo, nmo, nmo), dtype=np.float64)
        two_body_coefficients_ba = np.zeros(
            (nmo, nmo, nmo, nmo), dtype=np.float64)

        # transform alpha-alpha, beta-beta, alpha-beta and beta-alpha
        # 2-body coeffs into QCSchema format
        for p in range(nmo):
            for q in range(nmo):
                for r in range(nmo):
                    for s in range(nmo):

                        # alpha indexes
                        alpha_p = 2 * p + 1
                        alpha_q = 2 * q + 1
                        alpha_r = 2 * r + 1
                        alpha_s = 2 * s + 1

                        # beta indexes
                        beta_p = 2 * p + 2
                        beta_q = 2 * q + 2
                        beta_r = 2 * r + 2
                        beta_s = 2 * s + 2

                        if (alpha_p, alpha_q,
                                alpha_r, alpha_s) in two_body_integrals:

                            # alpha-alpha unique matrix element
                            aa_term = two_body_integrals[
                                (alpha_p, alpha_q, alpha_r, alpha_s)]

                            # exploiting perm symm of 2-body integrals
                            two_body_coefficients_aa[p, q, r, s] = aa_term
                            two_body_coefficients_aa[q, p, s, r] = aa_term
                            two_body_coefficients_aa[r, s, p, q] = np.conj(aa_term)
                            two_body_coefficients_aa[s, r, q, p] = np.conj(aa_term)

                            # restricted non-relativistic case
                            two_body_coefficients_ba[p, q, r, s] = aa_term
                            two_body_coefficients_ba[q, p, s, r] = aa_term
                            two_body_coefficients_ba[r, s, p, q] = np.conj(aa_term)
                            two_body_coefficients_ba[s, r, q, p] = np.conj(aa_term)

                            two_body_coefficients_ab[p, q, r, s] = aa_term
                            two_body_coefficients_ab[q, p, s, r] = aa_term
                            two_body_coefficients_ab[r, s, p, q] = np.conj(aa_term)
                            two_body_coefficients_ab[s, r, q, p] = np.conj(aa_term)

                            two_body_coefficients_bb[p, q, r, s] = aa_term
                            two_body_coefficients_bb[q, p, s, r] = aa_term
                            two_body_coefficients_bb[r, s, p, q] = np.conj(aa_term)
                            two_body_coefficients_bb[s, r, q, p] = np.conj(aa_term)

                        # non-restricted case ?
                        if (beta_p, beta_q,
                                beta_r, beta_s) in two_body_integrals:

                            # beta-beta unique matrix element
                            bb_term = two_body_integrals[
                                (beta_p, beta_q, beta_r, beta_s)]

                            two_body_coefficients_bb[p, q, r, s] = bb_term
                            two_body_coefficients_bb[q, p, s, r] = bb_term
                            two_body_coefficients_bb[r, s, p, q] = np.conj(bb_term)
                            two_body_coefficients_bb[s, r, q, p] = np.conj(bb_term)

                        if (alpha_p, beta_q,
                                beta_r, alpha_s) in two_body_integrals:

                            # alpha-beta unique matrix element
                            ab_term = two_body_integrals[
                                (alpha_p, beta_q, beta_r, alpha_s)]

                            two_body_coefficients_ab[p, q, r, s] = ab_term
                            two_body_coefficients_ab[q, p, s, r] = ab_term
                            two_body_coefficients_ab[r, s, p, q] = np.conj(ab_term)
                            two_body_coefficients_ab[s, r, q, p] = np.conj(ab_term)

                        if (beta_p, alpha_q,
                                alpha_r, beta_s) in two_body_integrals:

                            # beta-alpha unique matrix element
                            ba_term = two_body_integrals[
                                (beta_p, alpha_q, alpha_r, beta_s)]

                            two_body_coefficients_ba[p, q, r, s] = ba_term
                            two_body_coefficients_ba[q, p, s, r] = ba_term
                            two_body_coefficients_ba[r, s, p, q] = np.conj(ba_term)
                            two_body_coefficients_ba[s, r, q, p] = np.conj(ba_term)

        # truncate numbers lower than EQ_TOLERANCE
        two_body_coefficients_aa[np.abs(
            two_body_coefficients_aa) < EQ_TOLERANCE] = 0.
        two_body_coefficients_bb[np.abs(
            two_body_coefficients_bb) < EQ_TOLERANCE] = 0.
        two_body_coefficients_ab[np.abs(
            two_body_coefficients_ab) < EQ_TOLERANCE] = 0.
        two_body_coefficients_ba[np.abs(
            two_body_coefficients_ba) < EQ_TOLERANCE] = 0.

        wavefunction = file.require_group("wavefunction")
        wavefunction.attrs['basis'] = basis

        # 'scf_fock_mo_a/b' take the form of flattened nmo by nmo matrices.
        # E.g., for H2 [r(H-H) = 0.737166 angs] at RHF/sto-3g level,
        # scf_fock_mo_a = [-1.2550254253591242, 0.0, 0.0, -0.4732763494710688].
        wavefunction.create_dataset("scf_fock_mo_a",
                                    data=one_body_coefficients_a.flatten())
        wavefunction.create_dataset("scf_fock_mo_b",
                                    data=one_body_coefficients_b.flatten())

        # 'scf_eri_mo_aa/bb/ba/ab' take the form of flattened (nmo,nmo,nmo,nmo) matrices.
        # E.g., for H2 [r(H-H) = 0.737166 angs] at RHF/sto-3g level,
        # scf_fock_mo_aa = [0.6752967689354992, 0, 0, 0.6642044392432875,
        #                   0, 0.1810520713689906, 0.18105207136899099, 0,
        #                   0, 0.18105207136899074, 0.18105207136899115, 0,
        #                   0.6642044392432873, 0, 0, 0.6981738857839892].
        # For restricted cases: scf_fock_mo_aa = scf_fock_mo_bb =
        #                       scf_fock_mo_ba = scf_fock_mo_ab
        wavefunction.create_dataset("scf_eri_mo_aa",
                                    data=two_body_coefficients_aa.flatten())
        wavefunction.create_dataset("scf_eri_mo_bb",
                                    data=two_body_coefficients_bb.flatten())
        wavefunction.create_dataset("scf_eri_mo_ba",
                                    data=two_body_coefficients_ba.flatten())
        wavefunction.create_dataset("scf_eri_mo_ab",
                                    data=two_body_coefficients_ab.flatten())

        # possible future additions:
        # mo coefficients in AO basis
        wavefunction.create_dataset("scf_orbitals_a", data='')
        wavefunction.create_dataset("scf_orbitals_b", data='')
        # scf orbital energies
        wavefunction.create_dataset("scf_eigenvalues_a", data='')
        wavefunction.create_dataset("scf_eigenvalues_b", data='')
        # ROSE localized orbitals?
        wavefunction.create_dataset("localized_orbitals_a", data='')
        wavefunction.create_dataset("localized_orbitals_b", data='')

        file.close()

    def load(self, hdf5_filename: str) -> None:
        """Loads electronic structure data from a HDF5 file.

        Example:
        >>> from ase.build import molecule
        >>> from qc2.ase.dirac import DIRAC
        >>>
        >>> molecule = molecule('H2')
        >>> molecule.calc = DIRAC()     # => RHF/STO-3G
        >>> molecule.calc.load('h2.h5') # => instead of 'molecule.calc.get_potential_energy()'
        """
        BaseQc2ASECalculator.load(self, hdf5_filename)

    def get_integrals(self) -> Tuple[Union[float, complex],
                                     Dict[int, Union[float, complex]],
                                     Dict[Tuple[int, int], Union[float, complex]],
                                     Dict[Tuple[int, int, int, int], Union[float, complex]]]:
        """Retrieves 1- and 2-body integrals in MO basis from DIRAC FCIDUMP.

        Notes:
            Requires MRCONEE MDCINT files obtained using
            **DIRAC .4INDEX, **MOLTRA .ACTIVE all and 
            'pam ... --get="MRCONEE MDCINT"' options.

            Adapted from Openfermion-Dirac:
            see: https://github.com/bsenjean/Openfermion-Dirac.

        Returns:
            A tuple containing the following:
                - e_core (Union[float, complex]): Nuclear repulsion energy.
                - spinor (Dict[int, Union[float, complex]]): Dictionary of
                    spinor values with their corresponding indices.
                - one_body_int (Dict[Tuple[int, int], Union[float, complex]]):
                    Dictionary of one-body integrals with their corresponding
                    indices as tuples.
                - two_body_int (Dict[Tuple[int, int, int, int],
                    Union[float, complex]]): Dictionary of two-body integrals
                    with their corresponding indices as tuples.

        Raises:
            EnvironmentError: If the command execution fails.
            CalculationFailed: If the calculator fails with
                a non-zero error code.
        """
        command = "dirac_mointegral_export.x fcidump"
        try:
            proc = subprocess.Popen(command, shell=True, cwd=self.directory)
        except OSError as err:
            msg = f"Failed to execute {command}"
            raise EnvironmentError(msg) from err

        errorcode = proc.wait()

        if errorcode:
            path = os.path.abspath(self.directory)
            msg = (f"Calculator {self.name} failed with "
                   f"command {command} failed in {path} "
                   f"with error code {errorcode}")
            raise CalculationFailed(msg)

        e_core = 0
        spinor = {}
        one_body_int = {}
        two_body_int = {}
        num_lines = sum(1 for line in open("FCIDUMP"))
        with open('FCIDUMP') as f:
            start_reading = 0
            for line in f:
                start_reading += 1
                if "&END" in line:
                    break
            listed_values = [
                [token for token in line.split()] for line in f.readlines()]
            complex_int = False
            if len(listed_values[0]) == 6:
                complex_int = True
            if not complex_int:
                for row in range(num_lines-start_reading):
                    a_1 = int(listed_values[row][1])
                    a_2 = int(listed_values[row][2])
                    a_3 = int(listed_values[row][3])
                    a_4 = int(listed_values[row][4])
                    if a_4 == 0 and a_3 == 0:
                        if a_2 == 0:
                            if a_1 == 0:
                                e_core = float(listed_values[row][0])
                            else:
                                spinor[a_1] = float(listed_values[row][0])
                        else:
                            one_body_int[a_1, a_2] = float(
                                listed_values[row][0])
                    else:
                        two_body_int[a_1, a_2, a_3, a_4] = float(
                            listed_values[row][0])
            if complex_int:
                for row in range(num_lines-start_reading):
                    a_1 = int(listed_values[row][2])
                    a_2 = int(listed_values[row][3])
                    a_3 = int(listed_values[row][4])
                    a_4 = int(listed_values[row][5])
                    if a_4 == 0 and a_3 == 0:
                        if a_2 == 0:
                            if a_1 == 0:
                                e_core = complex(
                                   float(listed_values[row][0]),
                                   float(listed_values[row][1]))
                            else:
                                spinor[a_1] = complex(
                                   float(listed_values[row][0]),
                                   float(listed_values[row][1]))
                        else:
                            one_body_int[a_1, a_2] = complex(
                               float(listed_values[row][0]),
                               float(listed_values[row][1]))
                    else:
                        two_body_int[a_1, a_2, a_3, a_4] = complex(
                           float(listed_values[row][0]),
                           float(listed_values[row][1]))

        return e_core, spinor, one_body_int, two_body_int

    def _get_from_dirac_hdf5_file(self, property_name):
        """Helper routine to open dirac HDF5 output and extract property."""
        out_hdf5_file = self.prefix + "_" + self.prefix + ".h5"
        try:
            with h5py.File(out_hdf5_file, "r") as f:
                data = f[property_name][...]
        except (KeyError, IOError):
            data = None
        return data