#!/usr/bin/env python
# coding: utf-8

# In[1]:
import argparse, logging,calculator,json,mol_to_graph
from ase import io
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem
from ase import Atoms
import time,shutil,os,sys
# In[ ]:
# -------------------- input parameters --------------------
def parse_args():
    p = argparse.ArgumentParser(description="CI-NEB + volume by Multiwfn (Folder Isolated)")
    p.add_argument("--nimg",  type=int, default=3, help="number of images for NEB calculation")
    p.add_argument("--neb_fmax",  type=float, default=0.2, help="max force convergence threshold for NEB (eV/Å-1)")
    p.add_argument("--npara", type=int, default=4, help="number of parallel processes for Amesp")
    p.add_argument("--maxcore", type=int, default=4000, help="avilable memory (in MB) for Amesp")
    p.add_argument("--workdir", default="work_dirs", help="working directory")
    p.add_argument("--properties", default="HOMO-LUMO,charge,structure,rotational_constant,excited_energy", help="properties to extract, comma-separated")
    p.add_argument("--smiles",default=None,help="SMILES string")
    p.add_argument("--charge",type=int,default=0,help="Molecular charge (auto-detected from SMILES if not provided)")
    p.add_argument("--nstates",default=3,help="Number of excited states")
    p.add_argument("--excit_root",default=1,help="number of the excited state focused on")
    p.add_argument("--mult",default=1,help="multiplicity of the excited state")
    args, f = p.parse_known_args()
    return args


# In[3]:


def analysis(args,type_cal,log):
    import get_feature
    dirs = f'{args.workdir}/{type_cal}/{type_cal}'
    log.info(f"Analyzing {type_cal} calculation")
    features = {}
    features = get_feature.get_features_dict(open(dirs+'_run.aop').read(), type_cal, log,args)   
    return features

def smiles_to_ase_atoms(args, random_seed=42):
    """
    Convert SMILES string to ASE Atoms object (with robust 3D embedding).

    Parameters:
        smiles: SMILES string
        random_seed: Random seed for reproducible initial structure generation

    Returns:
        ASE Atoms object
    """

    # 1. Create RDKit molecule object from SMILES
    mol = Chem.MolFromSmiles(args.smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {args.smiles}")

    # 2. Add hydrogen atoms (required for 3D structure generation)
    mol = Chem.AddHs(mol)
    
    args.charge = Chem.GetFormalCharge(mol)

    # 3. Generate initial 3D coordinates with ETKDG (fallback to random coords)
    params = None
    if hasattr(AllChem, "ETKDGv3"):
        params = AllChem.ETKDGv3()
    elif hasattr(AllChem, "ETKDGv2"):
        params = AllChem.ETKDGv2()
    elif hasattr(AllChem, "ETKDG"):
        params = AllChem.ETKDG()

    if params is not None:
        params.randomSeed = random_seed
        if hasattr(params, "maxAttempts"):
            params.maxAttempts = 200
        res = AllChem.EmbedMolecule(mol, params)
        if res != 0 and hasattr(params, "useRandomCoords"):
            params.useRandomCoords = True
            res = AllChem.EmbedMolecule(mol, params)
    else:
        res = AllChem.EmbedMolecule(mol, randomSeed=random_seed)
        if res != 0:
            res = AllChem.EmbedMolecule(mol, randomSeed=random_seed, useRandomCoords=True)

    if res != 0 or mol.GetNumConformers() == 0:
        raise ValueError("RDKit embedding failed (no conformer generated)")

    # 4. Light geometry cleanup (does not replace quantum optimization)
    try:
        AllChem.UFFOptimizeMolecule(mol, maxIters=2000)
    except Exception:
        pass

    # 5. Get the conformation (3D coordinates)
    conf = mol.GetConformer()

    # 6. Extract atom information and create ASE Atoms object
    positions = []
    symbols = []

    for atom in mol.GetAtoms():
        # Get atom coordinates
        pos = conf.GetAtomPosition(atom.GetIdx())
        positions.append([pos.x, pos.y, pos.z])

        # Get element symbol
        symbols.append(atom.GetSymbol())

    # 7. Create ASE Atoms object
    atoms = Atoms(symbols=symbols, positions=positions)

    return atoms,args.charge


# In[5]:
# -------------------- main function -------------------
def main():
    # log setting
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    log = logging.getLogger(__name__)

    args = parse_args()

    # read in the initial and final structures
    if args.smiles:
        log.info("Start generating begin structure from SMILES")
        initial,args.charge = smiles_to_ase_atoms(args)
        log.info(f"Auto-detected charge from SMILES: {args.charge}")

    else:
        raise ValueError("SMILES string is required to generate initial structure.")

    start_time = time.time()

    if os.path.exists(args.workdir):
        shutil.rmtree(args.workdir+'/opt',ignore_errors=True)
        shutil.rmtree(args.workdir+'/excit',ignore_errors=True)
        shutil.rmtree(args.workdir+'/neb',ignore_errors=True)


    # 1. calculation
    opted_atoms=calculator.run_calculate(args, 'opt', initial,log)

    if not opted_atoms:
        log.error(f"Opt. Calculation failed. Exit code 1.")        
        sys.exit(1)

    
    excited_atoms = calculator.run_calculate(args, 'excit', opted_atoms,log)
    if not excited_atoms:
        log.error(f"Excit. Calculation failed. Exit code 2.")
        sys.exit(2)
        # 2. analysis features

    # optimized state features
    opted_features = analysis(args,'opt',log)
    charge=sum(opted_features['charge']['charge'])

    # excited state features
    excited_features = analysis(args,'excit',log)
    charge=sum(excited_features['charge']['charge'])
    
    #volume analysis.
    opted_features['volume'] = calculator.volume_Mutifwfn(f'{args.workdir}/opt/opted.xyz',log)
    excited_features['volume'] = calculator.volume_Mutifwfn(f'{args.workdir}/excit/excited.xyz',log)

    # 3. calculated diff charge
    results= {'charge':{'element' : excited_features['charge']['element'], 'charge_variation':None}}
    results['charge']['charge_variation'] = list(np.array(excited_features['charge']['charge'])-np.array(opted_features['charge']['charge']))
    # 4. NEB calculation

    neb_imgs = calculator.run_calculate(args, 'neb', opted_atoms,log, excited_atoms)
    if neb_imgs:
        volumes = calculator.compute_all_volumes(args,neb_imgs,log)
        neb_mean_volume = (sum(volumes)/len(volumes))
        if neb_mean_volume == ((float(excited_features['volume'])+float(opted_features['volume']))/float(args.nimg)) :
            log.error("NEB calculation failed. Exit code 3.")
            sys.exit(3)
    else:
        neb_mean_volume = None
        log.error("NEB calculation failed. Exit code 3.")
        sys.exit(3)



    # 5. generate the molecule graph
    try:
        mol_to_graph.mol_to_graph(f'{args.workdir}/opt/opted.xyz',opted_features['charge']['charge'],'opt',int(charge),args,log)
        mol_to_graph.mol_to_graph(f'{args.workdir}/excit/excited.xyz',excited_features["charge"]["charge"],'excit',int(charge),args,log)
    except Exception as e:
        log.info(f'Failed to generate the molecule graph. Error: {str(e)}')

    end_time = time.time()

    log.info(f"Running time: {end_time-start_time}")

    results.update({'ground_state' : opted_features, 'excited_state' : excited_features,'exciting_path_mean_volume': neb_mean_volume if neb_mean_volume else None})

    with open(args.workdir+'/result.json', 'w') as f:
        json.dump(results, f,indent=2)

# In[6]:
if __name__ == "__main__":
    main()
