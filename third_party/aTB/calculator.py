from PyAmesp import Amesp
from ase import io
from ase.mep import NEB
from ase.optimize import BFGS
from pathlib import Path

# -------------------- return the PyAmesp calculator --------------------
def make_amesp_calc(atoms, args,calculation_type,idx=None):
    """
    Based on the calculation type, return the PyAmesp calculator with command line,
    and specify an independent working directory. 
    """
    posthf = None
    if calculation_type == "opt":
        # define a work_dirs/opt/
        work_dir = Path(f"{args.workdir}/opt/")
        work_dir.mkdir(parents=True, exist_ok=True)
        label_path = work_dir / "opt_run"

        # define calculate options
        keywords=["atb", "opt","force"]
        opt = {'maxcyc': 2000, 'gediis': 'off', 'maxstep' : 0.3}

    elif calculation_type == "excit":
        # define a work_dirs/excit/
        work_dir = Path(f"{args.workdir}/excit/")
        work_dir.mkdir(parents=True, exist_ok=True)
        label_path = work_dir / "excit_run"

        # define calculate options
        keywords=["atb", "tda", 'opt',"force"]
        opt = {'maxcyc': 2000, 'gediis': 'off', 'maxstep' : 0.3}
        posthf = {'nstates': args.nstates, 'root': args.excit_root}
    
    elif calculation_type == "neb" :
        if idx == None:
            raise ValueError("For NEB calculation, idx must be provided.")
        else:
            # define a work_dirs/neb_img_XX/
            work_dir = Path(f"{args.workdir}/neb/neb_img_{idx:02d}/")
            work_dir.mkdir(parents=True, exist_ok=True)
            label_path = work_dir / "neb_run"

            # define calculate options
            keywords=["atb", "force"]
            opt = None  
    
    calc = Amesp(
        atoms=atoms,
        label=str(label_path), 
        command="amesp PREFIX.aip", 
        npara=args.npara,
        maxcore=args.maxcore,
        charge=0, mult=int(args.mult),
        keywords=keywords,
        opt = opt,
        scf = {'maxcyc':2000,'vshift': 500},
        posthf = posthf
    )
    return calc

def run_calculate(args, type, begin_atoms, log, end_atoms=None):
    """
    Execute different types of quantum chemical calculations.
    
    Args:
        args: Command line arguments object containing calculation configurations
        type: Calculation type, options: "neb" (Nudged Elastic Band), 
               "opt" (ground state optimization), "excit" (excited state optimization)
        begin_atoms: Initial atomic structure object
        log: Logger object for logging
        end_atoms: Required only for NEB calculation, final atomic structure object
    
    Returns:
        Depending on calculation type:
        - For NEB: List containing all interpolated images
        - For others: Optimized atomic structure object
    
    Raises:
        ValueError: If end_atoms is not provided for NEB calculation
    """
    
    if type == "neb":
        # NEB (Nudged Elastic Band) calculation: Find minimum energy path for reaction pathways
        if end_atoms is None:
            log.error("NEB calculation requires end_atoms parameter")
            raise ValueError("End atoms must be provided for NEB calculation.")
        
        log.info(f"Starting NEB calculation with {args.nimg} intermediate images")
        
        # 1. Initialize image sequence for NEB interpolation
        images = [begin_atoms]
        for i in range(args.nimg):
            images.append(begin_atoms.copy())
        images.append(end_atoms)
        log.debug(f"Created {len(images)} total images for NEB")
        
        # 2. Attach calculators to each image
        for idx, atoms in enumerate(images):
            atoms.calc = make_amesp_calc(atoms, args, 'neb', idx)
        log.debug("Calculators attached to all images")
        
        # 3. Build NEB object with climbing image and parallel processing
        neb = NEB(images, climb=True, parallel=True)
        neb.interpolate(method="idpp")
        log.debug("NEB object created and interpolated using IDPP method")
        
        # 4. Initialize optimizer and run NEB calculation
        dyn = BFGS(neb, 
                   trajectory=f"{args.workdir}/neb/neb.traj", 
                   logfile=f"{args.workdir}/neb/neb.log",
                   maxstep=0.1)
        log.info("Running NEB optimization...")
        dyn.run(fmax=args.neb_fmax)
        
        log.info("NEB calculation completed successfully")
        return images
        
    else:
        # Ground state or excited state optimization
        atoms = begin_atoms.copy()
        
        if type == "opt":
            log.info("Starting ground state optimization")
            atoms.calc = make_amesp_calc(atoms, args, 'opt')
        elif type == "excit":
            log.info("Starting excited state optimization")
            atoms.calc = make_amesp_calc(atoms, args, 'excit')
        
        # Perform initial calculation to set up the system
        log.debug("Performing initial energy calculation...")
        atoms.get_potential_energy()
        
        # Read calculation results
        elements, positions = atoms.calc.read_results()
        log.debug(f"Results obtained for {len(elements[-1])} atoms")
        
        # Write optimized structure to XYZ file
        output_file = f"{args.workdir}/{type}/{type}ed.xyz"
        with open(output_file, 'w') as f:
            f.write(f"{len(elements[-1])}\n")
            f.write("Generated from AOP output\n")
            for elem, pos in zip(elements[-1], positions[-1]):
                f.write(f"{elem:2s} {pos[0]:12.6f} {pos[1]:12.6f} {pos[2]:12.6f}\n")
        log.debug(f"Optimized structure written to {output_file}")
        
        # Read back the optimized structure
        atoms = io.read(output_file)
        log.info(f"{type} calculation completed successfully")
        
        return atoms
    
def volume_Mutifwfn(xyz):
    import subprocess
    """
    return the volume of xyz.file
    """
    cmd = "Multiwfn"
    stdin = f"{xyz}\n12\n0\nq\n"
    out = subprocess.run(cmd, input=stdin, text=True, capture_output=True)
    for line in out.stdout.splitlines():
        if line.strip().startswith("Volume:"):
            parts = line.split('(')
            parts = parts[-1].split(')')
            parts = parts[0].strip().split()

            if "Angstrom^3" in line:
                    idx = parts.index("Angstrom^3")
                    return float(parts[idx-1])
            
def compute_all_volumes(args,neb_imgs,log):
    import os
    out_dir=f"{args.workdir}/neb/volume_results/"
    os.makedirs(out_dir, exist_ok=True)
    
    for idx, at in enumerate(neb_imgs):
        io.write(out_dir+f"image_{idx:03d}.xyz", at)

    log.info("Computing volumes for all images")
    
    xyz_list = sorted(Path(out_dir).glob("image_*.xyz"))

    if not xyz_list:
        #log.error("Con't find xyz files, please run NEB or check neb_structures folder")
        return []

    vols = [volume_Mutifwfn(str(f)) for f in xyz_list]
    
    with open(out_dir+"volumes.log", "w") as fp:
        fp.write("Image\tVolume(Ang^3)\n")
        for f, v in zip(xyz_list, vols):
            fp.write(f"{f.stem}\t{v:.3f}\n")
    return vols
    
if __name__ == "__main__":

    import sys,argparse

    def parse_args():
        p = argparse.ArgumentParser(description="CI-NEB + volume by Multiwfn (Folder Isolated)")
        p.add_argument("--begin", default="begin.xyz", help="优化后的反应物")
        p.add_argument("--end",  default="end.xyz",  help="优化后的产物")
        p.add_argument("--nimg",  type=int, default=4, help="内插图像数（不含端点）")
        p.add_argument("--neb_fmax",  type=float, default=0.1, help="neb收敛阈值/eV Å-1")
        p.add_argument("--opt_fmax",  type=float, default=0.02, help="opt收敛阈值/eV Å-1")
        p.add_argument("--npara", type=int, default=1, help="amesp 并行核数")
        p.add_argument("--maxcore", type=int, default=100, help="每核内存/MB")
        p.add_argument("--method", default='LBFGS', help="优化器选择 Sella or LBFGS")
        p.add_argument("--post", default=False, help="只做后处理")
        args, f = p.parse_known_args()
        return args
    args = parse_args()

    # read in the initial and final structures
    initial = io.read(args.begin)
    try:
        compute_all_volumes()
        #run_calculate(args, 'excited', initial)
    except Exception as e:
        sys.exit(1)