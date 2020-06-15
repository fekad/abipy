# coding: utf-8
"""
RTA.nc file.
"""
import numpy as np
import abipy.core.abinit_units as abu

from monty.functools import lazy_property
from monty.string import marquee, list_strings
from abipy.core.mixins import AbinitNcFile, Has_Structure, Has_ElectronBands, NotebookWriter
from abipy.electrons.ebands import ElectronsReader, RobotWithEbands
from abipy.tools.plotting import add_fig_kwargs, get_ax_fig_plt, get_axarray_fig_plt
from abipy.abio.robots import Robot


__all__ = [
    "RtaFile",
    "RtaRobot",
]

def eh2s(eh):
    return {0: "n", 1: "p"}[eh]


def irta2s(irta):
    return {0: "SERTA", 1: "MRTA"}[irta]


def style_for_irta(irta, with_marker=False):
    """Line style for SERTA/MRTA"""
    if irta == 0:
        opts = dict(linewidth=1.0, linestyle="dotted")
        if with_marker: opts["marker"] = "^"
    elif irta == 1:
        opts = dict(linewidth=1.0, linestyle="solid")
        if with_marker: opts["marker"] = "v"
    else:
        raise ValueError("Invalid value for irta: %s" % irta)

    return opts


def transptens2latex(what, component):
    return {
        "sigma": r"$\sigma_{%s}$" % component,
        "seebeck": "$S_{%s}$" % component,
        "kappa": r"$\kappa^{\mathcal{e}}_{%s}$" % component,
        "pi": r"$\Pi_{%s}$" % component,
    }[what]


def irta2latextau(irta, with_dollars=False):
    s = r"\tau^{\mathbf{%s}}}" % irta2s(irta)
    if with_dollars: s = "$%s$" %s
    return s


def x2_grid(what_list):
    # Build grid of plots.
    what_list = list_strings(what_list)
    num_plots, ncols, nrows = len(what_list), 1, 1
    if num_plots > 1:
        ncols = 2
        nrows = (num_plots // ncols) + (num_plots % ncols)

    return num_plots, ncols, nrows, what_list


class RtaFile(AbinitNcFile, Has_Structure, Has_ElectronBands, NotebookWriter):

    @classmethod
    def from_file(cls, filepath):
        """Initialize the object from a netcdf file."""
        return cls(filepath)

    def __init__(self, filepath):
        super().__init__(filepath)
        self.reader = RtaReader(filepath)

        self.nrta = self.reader.read_dimvalue("nrta")

        #self.fermi = self.ebands.fermie * abu.eV_Ha
        #self.transport_ngkpt = self.reader.read_value("transport_ngkpt")
        #self.transport_extrael = self.reader.read_value("transport_extrael")
        #self.transport_fermie = self.reader.read_value("transport_fermie")

        # Get position of CBM and VBM for each spin in eV
        # nctkarr_t('vb_max', "dp", "nsppol")
        self.vb_max_spin = self.reader.read_value("vb_max") * abu.Ha_to_eV
        self.cb_min_spin = self.reader.read_value("cb_min") * abu.Ha_to_eV

        # Get metadata for k-integration (coming from edos%ncwrite)
        self.edos_intmeth = self.reader.read_value("edos_intmeth")
        self.edos_broad_eV = self.reader.read_value("edos_broad") * abu.Ha_to_eV

        # Store also the e-mesh n eV as it's often needed in the plotting routines.
        # Several quantitiies are defined on this mesh.
        self.edos_mesh_eV = self.reader.read_value("edos_mesh") * abu.Ha_to_eV

    @property
    def ntemp(self):
        """Number of temperatures."""
        return len(self.tmesh)

    @property
    def tmesh(self):
        """Mesh of Temperatures in Kelvin."""
        return self.reader.tmesh

    @lazy_property
    def ebands(self):
        """|ElectronBands| object."""
        return self.reader.read_ebands()

    @property
    def structure(self):
        """|Structure| object."""
        return self.ebands.structure

    @lazy_property
    def params(self):
        """:class:`OrderedDict` with parameters that might be subject to convergence studies."""
        od = self.get_ebands_params()
        return od

    def __str__(self):
        """String representation"""
        return self.to_string()

    def to_string(self, verbose=0):
        """String representation"""
        lines = []; app = lines.append

        app(marquee("File Info", mark="="))
        app(self.filestat(as_string=True))
        app("")
        app(self.structure.to_string(verbose=verbose, title="Structure"))
        app("")
        app(self.ebands.to_string(with_structure=False, verbose=verbose, title="KS Electron Bands"))
        app("")

        # Transport section.
        app(marquee("Transport calculation", mark="="))
        app("")
        app("edos_intmeth: %d" % self.edos_intmeth)
        app("edos_broad: %d (meV): " % (self.edos_broad_eV * 1000))
        app("mesh step for energy integrals: %.1f (meV) " % ((self.edos_mesh_eV[1] - self.edos_mesh_eV[0]) * 1000))
        app("")

        components = ("xx", "yy", "zz") if verbose == 0 else ("xx", "yy", "zz", "xy", "xz", "yx")
        for component in components:
            for irta in range(self.nrta):
                app("Mobility (%s Cartesian components), RTA type: %s" % (component, irta2s(irta)))
                app("Temperature [K]     Electrons (cm^2/Vs)     Holes (cm^2/Vs)")
                for itemp in range(self.ntemp):
                    temp = self.tmesh[itemp]
                    mobility_mu_e = self.get_mobility_mu(eh=0, itemp=itemp, component=component, irta=irta)
                    mobility_mu_h = self.get_mobility_mu(eh=1, itemp=itemp, component=component, irta=irta)
                    app("%14.1lf %18.6lf %18.6lf" % (temp, mobility_mu_e, mobility_mu_h))
                app("")

        return "\n".join(lines)

    def get_mobility_mu(self, eh, itemp, component='xx', ef=None, irta=0, spin=0):
        """
        Get the mobility at the chemical potential Ef

        Args:
            eh: 0 for electrons, 1 for holes.
            itemp: Index of the temperature.
            component: Cartesian component to plot: "xx", "yy" "xy" ...
            ef: Value of the doping in eV.
                The default None uses the chemical potential at the temperature item as computed by Abinit.
            spin: Spin index.
        """
        if ef is None: ef = self.reader.read_value('transport_mu_e')[itemp]
        emesh, mobility = self.reader.read_mobility(eh, itemp, component, spin, irta=irta)

        from scipy import interpolate
        f = interpolate.interp1d(emesh, mobility)
        return f(ef)

    #def _select_itemps_labels(self, obj):
    #   for it, temp in enumerate(self.tmesh):

    #def _select_irtas(self, obj):

    def _add_vline_at_bandedge(self, ax, spin, cbm_or_vbm, **kwargs):
        my_kwargs = dict(ymin=0, ymax=1, linewidth=1, linestyle="--")
        my_kwargs.update(kwargs)
        #from matplotlib.pyplot import text

        if cbm_or_vbm in ("cbm", "both"):
            x = self.cb_min_spin[spin]
            ax.axvline(x=x, color="red", **my_kwargs) # label="CBM",
            #ax.text(x, 5, "CBM", rotation=90, verticalalignment='center', fontsize=8)

        if cbm_or_vbm in ("vbm", "both"):
            x = self.vb_max_spin[spin]
            ax.axvline(x=x, color="blue", **my_kwargs) # label="VBM",
            #ax.text(x, 5, "VBM", rotation=90, verticalalignment='center', fontsize=8)

    @add_fig_kwargs
    def plot_edos(self, ax=None, fontsize=8, **kwargs):
        """
        Plot the electronic DOS

        Args:
            ax: |matplotlib-Axes| or None if a new figure should be created.

        Return: |matplotlib-Figure|
        """
        ax, fig, plt = get_ax_fig_plt(ax=ax)

        # Total DOS, spin up and spin down component.
        # nctkarr_t("edos_dos", "dp", "edos_nw, nsppol_plus1")
        dos = self.reader.read_value("edos_dos") / abu.Ha_to_eV

        # Plot total DOS.
        ax.plot(self.edos_mesh_eV, dos[0], label="Total DOS", color="black", linewidth=1.0)

        #idos = self.reader.read_value("edos_idos")
        #ax.plot(self.edos_mesh_eV, idos[0], label="Total IDOS", color="black", linewidth=1.0)

        if self.nsppol == 2:
            ax.plot(self.edos_mesh_eV, + dos[1], color="red", linewidth=1, label="up")
            ax.plot(self.edos_mesh_eV, - dos[2], color="blue", linewidth=1, label="down")

        for spin in range(self.nsppol):
            self._add_vline_at_bandedge(ax, spin, "both")

        ax.grid(True)
        ax.set_xlabel('Energy (eV)')
        ax.set_ylabel('States/eV p.u.c')
        ax.legend(loc="best", shadow=True, fontsize=fontsize)

        if "title" not in kwargs:
            title = r"$\frac{1}{N_k} \sum_{nk} \delta(\epsilon - \epsilon_{nk})$"
            fig.suptitle(title, fontsize=fontsize)

        return fig

    @add_fig_kwargs
    def plot_tau_isoe(self, ax_list=None, colormap="jet", fontsize=8, **kwargs):
        r"""
        Plot tau(e). Energy-dependent scattering rate defined by:

            $\tau(\epsilon) = \frac{1}{N_k} \sum_{nk} \tau_{nk}\,\delta(\epsilon - \epsilon_{nk})$

        Two differet subplots for SERTA and MRTA.

        Args:
            ax_list: List of |matplotlib-Axes| or None if a new figure should be created.

        Return: |matplotlib-Figure|
        """
        ax_list, fig, plt = get_axarray_fig_plt(ax_list, nrows=self.nrta, ncols=1,
                                                sharex=True, sharey=True, squeeze=False)
        ax_list = ax_list.ravel()
        cmap = plt.get_cmap(colormap)

        # nctkarr_t('tau_dos', "dp", "edos_nw, ntemp, nsppol, nrta")
        tau_dos = self.reader.read_value("tau_dos")

        for irta, ax in enumerate(ax_list):
            for spin in range(self.nsppol):
                spin_sign = +1 if spin == 0 else -1
                for it, temp in enumerate(self.tmesh):
                    # Convert to femtoseconds
                    ys = spin_sign * tau_dos[irta, spin, it] * abu.Time_Sec * 1e+15
                    ax.plot(self.edos_mesh_eV , ys, c=cmap(it / self.ntemp),
                            label="T = %dK" % temp if spin == 0 else None)

            ax.grid(True)
            ax.legend(loc="best", shadow=True, fontsize=fontsize)
            if irta  == (len(ax_list) - 1):
                ax.set_xlabel('Energy (eV)')
                ax.set_ylabel(r"$\tau(\epsilon)\, (fms)$")

            self._add_vline_at_bandedge(ax, spin, "both")

            ax.text(0.1, 0.9, irta2s(irta), fontsize=fontsize,
                horizontalalignment='center', verticalalignment='center', transform=ax.transAxes,
                bbox=dict(alpha=0.5))

        if "title" not in kwargs:
            title = r"$\tau(\epsilon) = \frac{1}{N_k} \sum_{nk} \tau_{nk}\,\delta(\epsilon - \epsilon_{nk})$"
            fig.suptitle(title, fontsize=fontsize)

        return fig

    #@add_fig_kwargs
    #def plot_vv_dos(self, component="xx", spin=0,
    #                 ax=None, fontsize=8, **kwargs):

    @add_fig_kwargs
    def plot_vvtau_dos(self, component="xx", spin=0,
                       ax=None, colormap="jet", fontsize=8, **kwargs):
        r"""
        Plot (v_i * v_j * tau) DOS.

            $\frac{1}{N_k} \sum_{nk} v_i v_j \delta(\epsilon - \epsilon_{nk})$

        Args:
            component: Cartesian component to plot: "xx", "yy" "xy" ...
            ax: |matplotlib-Axes| or None if a new figure should be created.
            colormap: matplotlib colormap.
            fontsize (int): fontsize for titles and legend

        Return: |matplotlib-Figure|
        """
        i, j = abu.s2itup(component)

        ax, fig, plt = get_ax_fig_plt(ax=ax)
        cmap = plt.get_cmap(colormap)

        for irta in range(self.nrta):
            # nctkarr_t('vvtau_dos', "dp", "edos_nw, three, three, ntemp, nsppol, nrta")
            var = self.reader.read_variable("vvtau_dos")
            for itemp, temp in enumerate(self.tmesh):
                vvtau_dos = var[irta, spin, itemp, j, i, :] / (2 * abu.Ha_s)
                label = "T = %dK" % temp
                if (itemp == 0): label = "%s (%s)" % (label, irta2s(irta))
                if (irta == 0 and itemp > 0): label = None
                ax.plot(self.edos_mesh_eV, vvtau_dos, c=cmap(itemp / self.ntemp), label=label, **style_for_irta(irta))

                #if itemp == 1:
                #    #nctkarr_t('vv_dos', "dp", "edos_nw, three, three, nsppol"), &
                #    var = self.reader.read_variable("vv_dos")
                #    vv_dos = var[spin, j, i, :] # / (2 * abu.Ha_s)
                #    ax.plot(self.edos_mesh_eV, vv_dos, c=cmap(itemp / self.ntemp), label='VVDOS' % temp)

        self._add_vline_at_bandedge(ax, spin, "both")

        ax.grid(True)
        ax.set_xlabel('Energy (eV)')
        ax.set_ylabel(r'$v_{%s} v_{%s} \tau$ DOS' % (component[0], component[1]))
        ax.set_yscale('log')
        ax.legend(loc="best", shadow=True, fontsize=fontsize)

        if "title" not in kwargs:
            vvt = r'v_{%s} v_{%s} \tau' % (component[0], component[1])
            title = r"$\frac{1}{N_k} \sum_{nk} %s\,\delta(\epsilon - \epsilon_{nk})$" % vvt
            fig.suptitle(title, fontsize=fontsize)

        return fig

    @add_fig_kwargs
    def plot_mobility(self, eh=0, irta=0, component='xx', spin=0, ax=None,
                      colormap='jet', fontsize=8, yscale="log", **kwargs):
        """
        Read the mobility from the netcdf file and plot it

        Args:
            component: Component to plot: "xx", "yy" "xy" ...
            ax: |matplotlib-Axes| or None if a new figure should be created.
            colormap: matplotlib colormap.
            fontsize (int): fontsize for titles and legend

        Return: |matplotlib-Figure|
        """
        ax, fig, plt = get_ax_fig_plt(ax=ax)
        cmap = plt.get_cmap(colormap)

        # nctkarr_t('mobility',"dp", "edos_nw, three, three, ntemp, two, nsppol, nrta")
        mu_var = self.reader.read_variable("mobility")
        i, j = abu.s2itup(component)

        for irta in range(self.nrta):
            for itemp, temp in enumerate(self.tmesh):
                mu = mu_var[irta, spin, eh, itemp, j, i]
                label = "T = %dK" % temp
                if (itemp == 0): label = "%s (%s)" % (label, irta2s(irta))
                if (irta == 0 and itemp > 0): label = None
                ax.plot(self.edos_mesh_eV, mu, c=cmap(itemp / self.ntemp), label=label, **style_for_irta(irta))

        self._add_vline_at_bandedge(ax, spin, "cbm" if eh == 0 else "vbm")

        ax.grid(True)
        ax.set_xlabel('Fermi level (eV)')
        ax.set_ylabel(r'%s-mobility $\mu_{%s}(\epsilon_F)$ (cm$^2$/Vs)' % (eh2s(eh), component))
        ax.set_yscale(yscale)
        ax.legend(loc="best", shadow=True, fontsize=fontsize)

        return fig

    @add_fig_kwargs
    def plot_transport_tensors_mu(self, component="xx", irta=0, spin=0,
                                  what_list=("sigma", "seebeck", "kappa", "pi"),
                                  colormap="jet", fontsize=8, **kwargs):
        """
        Plot selected Cartesian components of transport tensors as a function
        of the chemical potential mu at the given temperature.

        Args:
            ax_list: |matplotlib-Axes| or None if a new figure should be created.
            fontsize: fontsize for legends and titles

        Return: |matplotlib-Figure|
        """
        i, j = abu.s2itup(component)

        num_plots, ncols, nrows, what_list = x2_grid(what_list)
        ax_list, fig, plt = get_axarray_fig_plt(None, nrows=nrows, ncols=ncols,
                                                sharex=True, sharey=False, squeeze=False)
        ax_list = ax_list.ravel()
        # don't show the last ax if numeb is odd.
        if num_plots % ncols != 0: ax_list[-1].axis("off")

        cmap = plt.get_cmap(colormap)

        for iax, (what, ax) in enumerate(zip(what_list, ax_list)):
            irow, icol = divmod(iax, ncols)
            what_var = self.reader.read_variable(what)

            for irta in range(self.nrta):
                for itemp, temp in enumerate(self.tmesh):
                    ys = what_var[irta, spin, itemp, j, i]
                    label = "T = %dK" % temp
                    if (itemp == 0): label = "%s (%s)" % (label, irta2s(irta))
                    if (irta == 0 and itemp > 0): label = None
                    ax.plot(self.edos_mesh_eV, ys, c=cmap(itemp / self.ntemp), label=label, **style_for_irta(irta))

            ax.grid(True)
            ax.set_ylabel(transptens2latex(what, component))

            ax.legend(loc='best', fontsize=fontsize, shadow=True)
            if irow == nrows - 1:
                ax.set_xlabel("$\mu$ (eV)")

            self._add_vline_at_bandedge(ax, spin, "both")

        if "title" not in kwargs:
            fig.suptitle("Transport tensors", fontsize=fontsize)

        return fig

    def yield_figs(self, **kwargs):  # pragma: no cover
        """
        Return figures plotting the transport data
        """
        yield self.plot_tau_isoe(show=False)
        #yield self.plot_transport_tensors_mu(irta=0, show=False)
        yield self.plot_transport_tensors_mu(irta=1, show=False)
        yield self.plot_edos(show=False)
        yield self.plot_vvtau_dos(show=False)
        yield self.plot_mobility(show=False, title="Mobility")

    def close(self):
        """Close the file."""
        self.reader.close()

    def write_notebook(self, nbpath=None):
        """
        Write a jupyter_ notebook to ``nbpath``. If nbpath is None, a temporay file in the current
        working directory is created. Return path to the notebook.
        """
        nbformat, nbv, nb = self.get_nbformat_nbv_nb(title=None)

        nb.cells.extend([
            nbv.new_code_cell("ncfile = abilab.abiopen('%s')" % self.filepath),
            nbv.new_code_cell("print(ncfile)"),
            nbv.new_code_cell("ncfile.plot_edos();"),
            nbv.new_code_cell("ncfile.plot_vvtau_dos();"),
            nbv.new_code_cell("ncfile.plot_mobility();"),
        ])

        return self._write_nb_nbpath(nb, nbpath)


class RtaReader(ElectronsReader):
    """
    This class reads the results stored in the RTA.nc file
    It provides helper function to access the most important quantities.
    """
    def __init__(self, filepath):
        super().__init__(filepath)

        self.nsppol = self.read_dimvalue('nsppol')
        self.tmesh = self.read_value("kTmesh") / abu.kb_HaK

    #def read_vvdos_tau(self, itemp, component='xx', spin=0, irta=0):
    #    """
    #    Read the group velocity density of states times lifetime for different temperatures
    #    The vvdos_tau array has 4 dimensions (ntemp, 3, 3, nsppolplus1, nw)

    #      1. the number of temperatures
    #      2. 3x3 components of the tensor
    #      3. the spin polarization + 1 for the sum
    #      4. the number of frequencies
    #    """
    #    # nctkarr_t('vvtau_dos', "dp", "edos_nw, three, three, ntemp, nsppol, nrta")
    #    i, j = abu.s2itup(component)
    #    emesh = self.read_value("edos_mesh") * abu.Ha_eV
    #    vals = self.read_variable("vvtau_dos")
    #    vvtau_dos = vals[irta, spin, itemp, j, i, :] / (2 * abu.Ha_s)

    #    return emesh, vvtau_dos

    #def read_dos(self):
    #    """
    #    Read the density of states (in eV units)
    #    """
    #    # Total DOS, spin up and spin down component.
    #    # nctkarr_t("edos_dos", "dp", "edos_nw, nsppol_plus1")
    #    emesh = self.read_value("edos_mesh") * abu.Ha_to_eV
    #    dos = self.read_value("edos_dos") / abu.Ha_to_eV
    #    idos = self.read_value("edos_idos")

    #    #return ElectronDos(mesh, spin_dos, nelect)
    #    return emesh, dos, idos

    #def read_onsager(self, itemp):
    #    """
    #    Read the Onsager coefficients computed in the transport driver in Abinit
    #    """
    #    # nctkarr_t('L0', "dp", "edos_nw, three, three, ntemp, nsppol, nrta"), &
    #    L0 = np.moveaxis(self.read_variable("L0")[itemp,:], [0,1,2,3], [3,2,0,1])
    #    L1 = np.moveaxis(self.read_variable("L1")[itemp,:], [0,1,2,3], [3,2,0,1])
    #    L2 = np.moveaxis(self.read_variable("L2")[itemp,:], [0,1,2,3], [3,2,0,1])

    #    return L0, L1, L2

    #def read_transport(self, itemp):
    #    # nctkarr_t('sigma',   "dp", "edos_nw, three, three, ntemp, nsppol, nrta"), &
    #    sigma = np.moveaxis(self.read_variable("sigma")[itemp,:],     [0,1,2,3], [3,2,0,1])
    #    kappa = np.moveaxis(self.read_variable("kappa")[itemp,:],     [0,1,2,3], [3,2,0,1])
    #    seebeck = np.moveaxis(self.read_variable("seebeck")[itemp,:], [0,1,2,3], [3,2,0,1])
    #    pi = np.moveaxis(self.read_variable("pi")[itemp,:],           [0,1,2,3], [3,2,0,1])
    #    return sigma, kappa, seebeck, pi

    def read_mobility(self, eh, itemp, component, spin, irta=0):
        """
        Read mobility from the RTA.nc file
        The mobility is computed separately for electrons and holes.
        """
        # nctkarr_t('mobility',"dp", "edos_nw, three, three, ntemp, two, nsppol, nrta")
        i, j = abu.s2itup(component)
        wvals = self.read_variable("edos_mesh")
        #wvals = self.read_value("edos_mesh") * abu.Ha_eV
        mobility = self.read_variable("mobility")[irta, spin, eh, itemp, j, i, :]

        return wvals, mobility


class RtaRobot(Robot, RobotWithEbands):
    """
    This robot analyzes the results contained in multiple RTA.nc files.

    .. rubric:: Inheritance Diagram
    .. inheritance-diagram:: RtaRobot
    """

    EXT = "RTA"

    @add_fig_kwargs
    def plot_mobility_conv(self, eh=0, component='xx', itemp=0, spin=0,
                           fontsize=14, ax=None, **kwargs):
        """
        Plot the convergence of the mobility obtained in a list of files

        Args:
            eh: 0 for electrons, 1 for holes
            component: Component to plot ('xx', 'xy', ...)
            itemp: Index of the temperature.
            spin: Spin index.
            fontsize: fontsize for legends and titles
            ax: |matplotlib-Axes| or None if a new figure should be created.

        Returns: |matplotlib-Figure|
        """
        ax, fig, plt = get_ax_fig_plt(ax=ax)
        ax.grid(True)
        i, j = abu.s2itup(component)
        irta = 0

        res = []
        for ncfile in self.abifiles:
            kptrlatt  = ncfile.reader.read_value('kptrlatt')
            kptrlattx = kptrlatt[0, 0]
            kptrlatty = kptrlatt[1, 1]
            kptrlattz = kptrlatt[2, 2]
            # nctkarr_t('mobility_mu',"dp", "two, three, three, ntemp, nsppol, nrta")]
            mobility  = ncfile.reader.read_variable('mobility_mu')[irta, spin, itemp, j, i, eh]
            res.append([kptrlattx, mobility])

        res.sort(key=lambda t: t[0])
        res = np.array(res)

        size = 14
        if eh == 0:
            ax.set_ylabel(r'Electron mobility (cm$^2$/(V$\cdot$s))', size=size)
        elif eh == 1:
            ax.set_ylabel(r'Hole mobility (cm$^2$/(V$\cdot$s))', size=size)
        else:
            raise ValueError("Invalid value for `eh` argument: %s" % eh)

        from fractions import Fraction
        ratio1 = Fraction(kptrlatty, kptrlattx)
        ratio2 = Fraction(kptrlattz, kptrlattx)
        text1  = '' if ratio1.numerator == ratio1.denominator else \
                 r'$\frac{{{0}}}{{{1}}}$'.format(ratio1.numerator, ratio1.denominator)
        text2  = '' if ratio2.numerator == ratio2.denominator else \
                 r'$\frac{{{0}}}{{{1}}}$'.format(ratio2.numerator, ratio2.denominator)

        ax.set_xlabel(r'Homogeneous $N_k \times$ '+ text1 + r'$N_k \times$ '+ text2 + r'$N_k$ $\mathbf{k}$-point grid',
                      size=size)

        ax.plot(res[:,0], res[:,1], **kwargs)

        ax.legend(loc="best", shadow=True, fontsize=fontsize)

        return fig

    def yield_figs(self, **kwargs):  # pragma: no cover
        """
        This function *generates* a predefined list of matplotlib figures with minimal input from the user.
        Used in abiview.py to get a quick look at the results.
        """
        #yield self.plot_lattice_convergence(show=False)
        #yield self.plot_gsr_convergence(show=False)
        #for fig in self.get_ebands_plotter().yield_figs(): yield fig
        #self.plot_mobility_conv(eh=0, component='xx', itemp=0, spin=0, fontsize=14, ax=None, **kwargs):

    #def get_panel(self):
    #    """
    #    Build panel with widgets to interact with the |GsrRobot| either in a notebook or in panel app.
    #    """
    #    from abipy.panels.transportfile import TransportRobotPanel
    #    return TransportRobotPanel(self).get_panel()

    def write_notebook(self, nbpath=None):
        """
        Write a jupyter_ notebook to ``nbpath``. If nbpath is None, a temporay file in the current
        working directory is created. Return path to the notebook.
        """
        nbformat, nbv, nb = self.get_nbformat_nbv_nb(title=None)

        args = [(l, f.filepath) for l, f in self.items()]
        nb.cells.extend([
            #nbv.new_markdown_cell("# This is a markdown cell"),
            nbv.new_code_cell("robot = abilab.GsrRobot(*%s)\nrobot.trim_paths()\nrobot" % str(args)),
            #nbv.new_code_cell("ebands_plotter = robot.get_ebands_plotter()"),
        ])

        # Mixins
        #nb.cells.extend(self.get_baserobot_code_cells())
        #nb.cells.extend(self.get_ebands_code_cells())

        return self._write_nb_nbpath(nb, nbpath)


if __name__ == "__main__":
    import sys
    robot = RtaRobot.from_files(sys.argv[1:])
    print(robot)

    #import matplotlib.pyplot as plt
    #plt.figure(0, figsize=(14,9))
    #plt.tick_params(labelsize=14)
    #ax = plt.gca()

    robot.plot_mobility_conv(ax=None, color='k', label=r'$N_{{q_{{x,y,z}}}}$ = $N_{{k_{{x,y,z}}}}$')

    #fileslist = ['conv_fine/k27x27x27/q27x27x27/Sio_DS1_TRANSPORT.nc',
    #             'conv_fine/k30x30x30/q30x30x30/Sio_DS1_TRANSPORT.nc',
    #             'conv_fine/k144x144x144/q144x144x144/Sio_DS1_TRANSPORT.nc',]

    #plot_mobility_conv(ax, fileslist, color='k', marker='o', label=r'$N_{{q_{{x,y,z}}}}$ = $N_{{k_{{x,y,z}}}}$')

    #fileslist = ['conv_fine/k27x27x27/q54x54x54/Sio_DS1_TRANSPORT.nc',
    #             'conv_fine/k66x66x66/q132x132x132/Sio_DS1_TRANSPORT.nc',
    #             'conv_fine/k72x72x72/q144x144x144/Sio_DS1_TRANSPORT.nc']

    #plot_mobility_conv(ax, fileslist, color='r', marker='x', label=r'$N_{{q_{{x,y,z}}}}$ = $2 N_{{k_{{x,y,z}}}}$')

    #plt.legend(loc='best',fontsize=14)
    #plt.show()
