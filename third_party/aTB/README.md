# 1. 使用前的准备：
## a. 下载Amesp和PyAmesp
由于aTB是内置在Amesp中的，所以需要下载Amesp，解压Amesp_2.1_dev.zip
    
```
unzip Amesp_2.1_dev.zip
```
添加环境变量，当前我解压后的路径是在**/mnt/afs/250010045/soft/Amesp/Bin**，例如
```
export PATH=$PATH:/mnt/afs/250010045/soft/Amesp/Bin
```
之后使用以下命令下载PyAmesp用于Python与Amesp的链接
```
git clone https://github.com/DYang90/PyAmesp.git
```
我安装的路径为 **/mnt/afs/250010045/soft/PyAmesp**，以此为例，由于一些正则表达式不够准确，因此我进行了一些修改，在这一步需要将github中的**amesp.py**将原本的**PyAmesp/PyAmesp/amesp.py替换后，在PyAmesp/文件夹下运行以下命令，为Python安装此包
```
pip install .
```
除此之外，还需要安装其他包：
```
pip install scikit-learn ase
``` 
## b. 下载Multiwfn
期间会使用到Multiwfn进行体积计算，因此需要下载安装该软件包，同样解压
```
unzip Multiwfn_3.8_dev_bin_Linux_noGUI
```
我的安装路径为例子**/mnt/afs/250010045/soft/Multiwfn/**，需要进行以下设置
```
chmod +x /mnt/afs/250010045/soft/Multiwfn/Multiwfn
export Multiwfnpath=/mnt/afs/250010045/soft/Multiwfn/
export PATH=$PATH:/mnt/afs/250010045/soft/Multiwfn/
```
为提高Multiwfn的并行效率，我进行了一些更改，也可以将github中的**Multiwfn**文件进行相应的代替，为了保证有足够的内存进行aTB和Multiwfn运算还需要设置
```
export KMP_STACKSIZE=4g
ulimit -c unlimited
ulimit -s unlimited
```

# 2.使用方法
## a. 代码使用参数
### 结构输入
仅需要输入结构文件（.xyz, .cif, CONTCAR）或者SMILES式，SMILES式需要用引号引住，关键词如下
```
--begin: 初始结构文件路径（默认: begin.xyz）
--smiles: SMILES字符串（优先使用）
```
### 计算参数
```
--nimg: NEB中间图像数量（默认: 3）
--neb_fmax: NEB收敛阈值（默认: 0.1 eV/Å）
--opt_fmax: 结构优化收敛阈值（默认: 0.02 eV/Å）
--npara: 并行计算核数（默认: 4）
--maxcore: 每核内存限制（MB，默认: 4000 约 4GB）
--nstates: 激发态数量（默认: 3）
--excit_root: 关注的激发态根数（默认: 1）
--mult: 自旋多重度（默认: 1） 对于带电体系需要进行设置
```
### 输出参数
--properties: 要提取的特征（逗号分隔）默认为 "HOMO-LUMO,charge,structure"，体积计算不需要指定
--workdir: 工作目录（默认: work_dirs）
### 输出结构
```
{
  "ground_state": {                    // 对象，表示基态信息
    "HOMO-LUMO": "字符串",             // 字符串，表示HOMO-LUMO能隙
    "charge": {                        // 对象，表示电荷分布
      "element": [ "字符串数组" ],     // 数组，元素列表
      "charge": [ "数字数组" ]         // 数组，包含对应原子的电荷值
    },
    "structure": {                     // 对象，表示结构参数
      "bonds": 数字,                   // 数字，表示键长相关参数
      "angles": 数字,                  // 数字，表示键角相关参数
      "DA": 数字                       // 数字，表示二面角或其他参数
    },
    "volume": 数字                     // 数字，表示体积
  },
  "excited_state": {                   // 对象，表示激发态信息，结构与ground_state类似
    "HOMO-LUMO": "字符串",
    "charge": {
      "element": [ "字符串数组" ],
      "charge": [ "数字数组" ]
    },
    "structure": {
      "bonds": 数字,
      "angles": 数字,
      "DA": 数字
    },
    "volume": 数字
  },
  "NEB": 数字                          // 数字，表示NEB过程中平均体积
}
```
### 输出文件夹结构
```
.
├── main.py              # 主程序入口
├── calculator.py        # 计算器设置和NEB计算
├── get_feature.py       # 特征提取功能
└── work_dirs/          # （自动生成）
    ├── opt/            # 基态优化结果
    ├── excit/          # 激发态优化结果  
    └── neb/           # NEB计算结果
    └── results.json   # 保存所有计算结果
```

## b. 使用举例 
对于第二个分子，有SMILES式为***CCCCN(CCCC)c1ccc(/C=N/C(C#N)=C(N)/C#N)cc1***, 我想把输出结果放在**2**文件夹下，运行代码可以按以下进行
```
python main.py --smiles 'CCCCN(CCCC)c1ccc(/C=N/C(C#N)=C(N)/C#N)cc1' --workdir './2'
```
完整的计算输出如下，最终输出**NEB mean volume**即为成功进行所有计算：
```
2026-01-07 07:42:56,479 | Start generating begin structure from SMILES
2026-01-07 07:42:57,320 | Starting ground state optimization
2026-01-07 07:43:11,290 | opt calculation completed successfully
2026-01-07 07:43:11,292 | Analyzing opt calculation
2026-01-07 07:43:15,003 | Starting excited state optimization
2026-01-07 07:43:21,957 | excit calculation completed successfully
2026-01-07 07:43:21,958 | Analyzing excit calculation
2026-01-07 07:43:25,688 | Starting NEB calculation with 3 intermediate images
2026-01-07 07:43:25,717 | Running NEB optimization...
2026-01-07 07:44:09,731 | NEB calculation completed successfully
2026-01-07 07:44:09,756 | Computing volumes for all images
2026-01-07 07:44:28,511 | NEB mean volume: 512.91852
```
