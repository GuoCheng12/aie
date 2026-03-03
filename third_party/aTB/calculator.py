from PyAmesp import Amesp
from ase import io
from ase.mep import NEB
from ase.mep import NEBTools
from ase.optimize import LBFGS
from pathlib import Path

def detect_state(lines,log):
    if not lines:
        log.error("计算结果文件为空，无法判断计算状态")
        return False

    if 'Stop' in lines[-1].strip().split():
        log.error("Calculation did not complete successfully.")
        return False
    else:
        return True

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
        opt = {'maxcyc': 2000, 'gediis': 'off', 'maxstep' : 0.1,'geomtol': 'loose'}

    elif calculation_type == "excit":
        # define a work_dirs/excit/
        work_dir = Path(f"{args.workdir}/excit/")
        work_dir.mkdir(parents=True, exist_ok=True)
        label_path = work_dir / "excit_run"

        # define calculate options
        keywords=["atb", "tda", 'opt',"force"]
        opt = {'maxcyc': 2000, 'gediis': 'off', 'maxstep' : 0.1,'geomtol': 'loose'}
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
        charge=args.charge, mult=int(args.mult),
        keywords=keywords,
        opt = opt,
        scf = {'maxcyc':2000,'vshift': 500},
        posthf = posthf
    )
    return calc

def run_calculate(args, type_calc, begin_atoms, log, end_atoms=None):
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
    
    if type_calc == "neb":
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
        dyn = LBFGS(neb, 
                   trajectory=f"{args.workdir}/neb/neb.traj", 
                   logfile=f"{args.workdir}/neb/neb.log",
                   maxstep=0.05,
                   memory=200
                   )
        log.info("Running NEB optimization...")
        try:
            dyn.run(fmax=args.neb_fmax, steps=300)
            neb_atoms = NEBTools(images)
            if neb_atoms.get_fmax() <= args.neb_fmax:
                log.info("NEB optimization converged successfully")
                return images
            else:
                log.error("NEB optimization did not converge within the maximum steps")
                return None
        except Exception as e:
            log.error(f"NEB calculation failed: {str(e)}")
            raise e
        
    else:
        # Ground state or excited state optimization
        atoms = begin_atoms.copy()

        if type_calc == "opt":
            log.info("Starting ground state optimization")
            atoms.calc = make_amesp_calc(atoms, args, 'opt')
        elif type_calc == "excit":
            log.info("Starting excited state optimization")
            atoms.calc = make_amesp_calc(atoms, args, 'excit')
        
        # Perform initial calculation to set up the system
        try:
            atoms.get_potential_energy()
        except Exception as e:
            log.error(f"{type_calc} calculation failed")
            return None

        with open(f"{args.workdir}/{type_calc}/{type_calc}_run.aop",'r') as aop_f:
            lines = aop_f.readlines() # skip header line
            aop_file = [line.rstrip('\n') for line in lines if line.strip()]

            if not detect_state(aop_file,log):
                log.error(f"{type_calc} calculation did not complete successfully.")
                return None

        aop_f.close()

        # Read calculation results
        elements, positions = atoms.calc.read_results()
        
        # Write optimized structure to XYZ file
        output_file = f"{args.workdir}/{type_calc}/{type_calc}ed.xyz"
        with open(output_file, 'w') as f:
            f.write(f"{len(elements[-1])}\n")
            f.write("Generated from AOP output\n")
            for elem, pos in zip(elements[-1], positions[-1]):
                f.write(f"{elem:2s} {pos[0]:12.6f} {pos[1]:12.6f} {pos[2]:12.6f}\n")
        
        # Read back the optimized structure
        atoms = io.read(output_file)
        log.info(f"{type_calc} calculation completed successfully")
        
        return atoms
    
def volume_Mutifwfn(xyz,log):
    import subprocess
    """
    return the volume of xyz.file
    """
    cmd = "Multiwfn"
    stdin = f"{xyz}\n12\n0\nq\n"
    try:
        out = subprocess.run(cmd, input=stdin, text=True, capture_output=True)
    except Exception as e:
        log.error(f"Error running Multiwfn: {str(e)}")
        raise e

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
        log.error("Con't find xyz files, please run NEB or check neb_structures folder")
        return None

    vols = [volume_Mutifwfn(str(f),log) for f in xyz_list]
    
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

    v=volume_Mutifwfn('work_dirs/opt/opted.xyz')
    print(v)