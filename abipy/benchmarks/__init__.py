from __future__ import print_function, division, unicode_literals, absolute_import

import os

from monty.termcolor import cprint
from pymatgen.io.abinit.flows import Flow


def bench_main(main):
    """
    This decorator is used to decorate main functions producing `AbinitFlows`.
    It adds the initialization of the logger and an argument parser that allows one to select 
    the loglevel, the workdir of the flow as well as the YAML file with the parameters of the `TaskManager`.
    The main function shall have the signature:

        main(options)

    where options in the container with the command line options generated by `ArgumentParser`.

    Args:
        main:
            main function.
    """
    from functools import wraps

    @wraps(main)
    def wrapper(*args, **kwargs):
        import argparse
        parser = argparse.ArgumentParser()

        parser.add_argument('--loglevel', default="ERROR", type=str,
                            help="set the loglevel. Possible values: CRITICAL, ERROR (default), WARNING, INFO, DEBUG")

        parser.add_argument('-v', '--verbose', default=0, action='count', # -vv --> verbose=2
                                  help='verbose, can be supplied multiple times to increase verbosity')

        parser.add_argument("-w", '--workdir', default="", type=str, help="Working directory of the flow.")

        parser.add_argument("-m", '--manager', default=None, 
                            help="YAML file with the parameters of the task manager. " 
                                 "Default None i.e. the manager is read from standard locations: "
                                 "working directory first then ~/.abinit/abipy/manager.yml.")

        parser.add_argument("--mpi-list", default=None, help="List of MPI processors to be tested."
                            "'--mpi-list='(1,4,2)' performs benchmarks for mpi_procs in [1, 3]")

        parser.add_argument("--omp-list", default=None, help="List of OMP threads to be tested."
                            "'--omp-list='(1,4,2)' performs benchmarks for omp_threads in [1, 3]")

        parser.add_argument("--min-ncpus", default=-1, type=int, help="Minimum number of CPUs to be tested.")
        parser.add_argument("--max-ncpus", default=206, type=int, help="Maximum number of CPUs to be tested.")
        parser.add_argument("--min-eff", default=0.6, type=int, help="Minimum parallel efficiency accepted.")

        parser.add_argument('--paw', default=False, action="store_true", help="Run PAW calculation if present")

        parser.add_argument("-i", '--info', default=False, action="store_true", help="Show benchmark info and exit")
        parser.add_argument("-r", "--remove", default=False, action="store_true", help="Remove old flow workdir")

        parser.add_argument("--scheduler", "-s", default=False, action="store_true", help="Run with the scheduler")

        options = parser.parse_args()

        # loglevel is bound to the string value obtained from the command line argument. 
        # Convert to upper case to allow the user to specify --loglevel=DEBUG or --loglevel=debug
        import logging
        numeric_level = getattr(logging, options.loglevel.upper(), None)
        if not isinstance(numeric_level, int):
            raise ValueError('Invalid log level: %s' % options.loglevel)
        logging.basicConfig(level=numeric_level)

        # parse arguments
        if options.mpi_list is not None:
            import ast
            t = ast.literal_eval(options.mpi_list)
            assert len(t) == 3
            options.mpi_list = range(t[0], t[1], t[2])
            #print(options.mpi_list)

        if options.omp_list is not None:
            import ast
            t = ast.literal_eval(options.omp_list)
            assert len(t) == 3
            options.omp_list = range(t[0], t[1], t[2])
            #print(options.omp_list)

        # Monkey patch options to add useful method 
        #   accept_mpi_omp(mpi_proc, omp_threads)
        def monkey_patch(opts):
            def accept_mpi_omp(opts, mpi_procs, omp_threads):
                """Return True if we can run a benchmark with mpi_procs and omp_threads"""
                tot_ncpus = mpi_procs * omp_threads
                if tot_ncpus < opts.min_ncpus:
                    cprint("Skipping mpi_procs %d because of min_ncpus" % mpi_procs, color="magenta")
                    return False
                if opts.max_ncpus is not None and tot_ncpus > opts.max_ncpus:
                    cprint("Skipping mpi_procs %d because of max_ncpus" % mpi_procs, color="magenta")
                    return False
                return True 

            import types
            opts.accept_mpi_omp = types.MethodType(accept_mpi_omp, opts)

            def get_workdir(opts, _file_):
                """
                Return the workdir of the benchmark. 
                A default value if constructed from the name of the scrip if no cmd line arg.
                """
                if options.workdir: return options.workdir
                return "bench_" + os.path.basename(_file_).replace(".py", "")

            opts.get_workdir = types.MethodType(get_workdir, opts)
            
        monkey_patch(options)

        # Istantiate the manager.
        from abipy.abilab import TaskManager
        options.manager = TaskManager.as_manager(options.manager)

        flow = main(options)
        if flow is None: return 0

        if options.scheduler:
            return flow.make_scheduler().start()

        return 0

    return wrapper


class BenchmarkFlow(Flow):

    def exclude_from_benchmark(self, node):
        """Exclude a task or the tasks in a Work from the benchmark analysis."""
        if not hasattr(self, "_exclude_nodeids"): self._exclude_nodeids = set()

        if node.is_work:
            for task in node:
                self._exclude_nodeids.add(task.node_id)
        else:
            assert node.is_task
            self._exclude_nodeids.add(node.node_id)

    @property
    def exclude_nodeids(self):
        if not hasattr(self, "_exclude_nodeids"): self._exclude_nodeids = set()
        return self._exclude_nodeids 

    def get_parser(self):
        """
        Parse the timing sections in the output files.
        Return AbinitTimerParser parser object for further analysis.
        """
        nids = []
        for task in self.iflat_tasks():
            if task.node_id in self.exclude_nodeids: continue
            if task.status != task.S_OK: continue
            #print("analysing task:", task)
            nids.append(task.node_id)

        parser = self.parse_timing(nids=nids)

        if parser is None: 
            print("parse_timing returned None!")
        else:
            if len(parser) != len(nids): 
                print("Not all timing sections have been parsed!")

        return parser

    #def make_tarball(self):
    #    self.make_tarfile(self, name=None, max_filesize=None, exclude_exts=None, exclude_dirs=None, verbose=0, **kwargs):

