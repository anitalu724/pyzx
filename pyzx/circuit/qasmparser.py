# PyZX - Python library for quantum circuit rewriting 
#        and optimization using the ZX-calculus
# Copyright (C) 2018 - Aleks Kissinger and John van de Wetering

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#    http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import math
from fractions import Fraction
from typing import List, Dict, Tuple, Optional

from . import Circuit
from .gates import Gate, qasm_gate_table, ZPhase, XPhase, CRZ, CP

class QASMParser(object):
    """Class for parsing QASM source files into circuit descriptions."""
    def __init__(self) -> None:
        self.gates: List[Gate] = []
        self.customgates: Dict[str,Circuit] = {}
        self.registers: Dict[str,Tuple[int,int]] = {}
        self.qubit_count: int = 0
        self.circuit: Optional[Circuit] = None

    def parse(self, s: str, strict:bool=True) -> Circuit:
        lines = s.splitlines()
        r = []
        #strip comments
        for s in lines:
            if s.find("//")!=-1:
                t = s[0:s.find("//")].strip()
            else: t = s.strip()
            if t: r.append(t)

        if r[0].startswith("OPENQASM"):
            r.pop(0)
        elif strict:
            raise TypeError("File does not start with OPENQASM descriptor")

        if r[0].startswith('include "qelib1.inc";'):
            r.pop(0)
        elif strict:
            raise TypeError("File is not importing standard library")

        data = "\n".join(r)
        # Strip the custom command definitions from the normal commands
        while True:
            i = data.find("gate ")
            if i == -1: break
            j = data.find("}", i)
            self.parse_custom_gate(data[i:j+1])
            data = data[:i] + data[j+1:]
        #parse the regular commands
        commands = [s.strip() for s in data.split(";") if s.strip()]
        gates: List[Gate] = []
        for c in commands:
            self.gates.extend(self.parse_command(c, self.registers))

        circ = Circuit(self.qubit_count)
        circ.gates = self.gates
        self.circuit = circ
        return self.circuit

    def parse_custom_gate(self, data: str) -> None:
        data = data[5:]
        spec, body = data.split("{",1)
        if "(" in spec:
            i = spec.find("(")
            j = spec.find(")")
            if spec[i+1:j].strip():
                raise TypeError("Arguments for custom gates are currently"
                                " not supported: {}".format(data))
            spec = spec[:i] + spec[j+1:]
        spec = spec.strip()
        if " " in spec:
            name, args = spec.split(" ",1)
            name = name.strip()
            args = args.strip()
        else:
            raise TypeError("Custom gate specification doesn't have any "
                            "arguments: {}".format(data))
        registers : Dict[str,Tuple[int,int]] = {}
        qubit_count = 0
        for a in args.split(","):
            a = a.strip()
            if a in registers:
                raise TypeError("Duplicate variable name: {}".format(data))
            registers[a] = (qubit_count,1)
            qubit_count += 1

        body = body[:-1].strip()
        commands = [s.strip() for s in body.split(";") if s.strip()]
        circ = Circuit(qubit_count)
        for c in commands:
            for g in self.parse_command(c, registers):
                circ.add_gate(g)
        self.customgates[name] = circ

    def parse_command(self, c: str, registers: Dict[str,Tuple[int,int]]) -> List[Gate]:
        gates: List[Gate] = []
        name, rest = c.split(" ",1)
        if name in ("barrier","creg","measure", "id"): return gates
        if name in ("opaque", "if"):
            raise TypeError("Unsupported operation {}".format(c))
        args = [s.strip() for s in rest.split(",") if s.strip()]
        if name == "qreg":
            regname, sizep = args[0].split("[",1)
            size = int(sizep[:-1])
            registers[regname] = (self.qubit_count, size)
            self.qubit_count += size
            return gates
        qubit_values = []
        is_range = False
        dim = 1
        for a in args:
            if "[" in a:
                regname, valp = a.split("[",1)
                val = int(valp[:-1])
                if not regname in registers: raise TypeError("Invalid register {}".format(regname))
                qubit_values.append([registers[regname][0]+val])
            else:
                if is_range:
                    if registers[a][1] != dim:
                        raise TypeError("Error in parsing {}: Register sizes do not match".format(c))
                else:
                    dim = registers[a][1]
                is_range = True
                s = registers[a][0]
                qubit_values.append(list(range(s,s + dim)))
        if is_range:
            for i in range(len(qubit_values)):
                if len(qubit_values[i]) != dim:
                    qubit_values[i] = [qubit_values[i][0]]*dim
        for j in range(dim):
            argset = [q[j] for q in qubit_values]
            if name in self.customgates:
                circ = self.customgates[name]
                if len(argset) != circ.qubits:
                    raise TypeError("Argument amount does not match gate spec: {}".format(c))
                for g in circ.gates:
                    gates.append(g.reposition(argset))
                continue
            if name in ("x", "z", "s", "t", "h", "sdg", "tdg"):
                if name in ("sdg", "tdg"): 
                    g = qasm_gate_table[name](argset[0],adjoint=True) # type: ignore # mypy can't handle -
                else: g = qasm_gate_table[name](argset[0]) # type: ignore # - Gate subclasses with different numbers of parameters
                gates.append(g)
                continue
            if name.startswith("rx") or name.startswith("rz") or name.startswith("u1") or name.startswith('crz') or name.startswith('cp'):
                i = name.find('(')
                j = name.find(')')
                if i == -1 or j == -1: raise TypeError("Invalid specification {}".format(name))
                valp = name[i+1:j]
                # try:
                #     phasep = float(valp)/math.pi
                # except ValueError:
                #     if valp.find('pi') == -1: raise TypeError("Invalid specification {}".format(name))
                #     valp = valp.replace('pi', '')
                #     valp = valp.replace('*','')
                #     try: phasep = float(valp)
                #     except: raise TypeError("Invalid specification {}".format(name))
                # phase = Fraction(phasep).limit_denominator(100000000)
                phase = self.parse_phase_arg(valp)
                if name.startswith('rx'): g = XPhase(argset[0],phase=phase)
                elif name.startswith('crz'): g = CRZ(argset[0],argset[1],phase=phase)
                elif name.startswith('cp'): g = CP(argset[0],argset[1],phase=phase)
                else: g = ZPhase(argset[0],phase=phase)
                gates.append(g)
                continue
            if name.startswith('u2') or name.startswith('u3'): # see https://arxiv.org/pdf/1707.03429.pdf
                i = name.find('(')
                j = name.find(')')
                if i == -1 or j == -1: raise TypeError("Invalid specification {}".format(name))
                vals = name[i+1:j].split(',')
                phases = [self.parse_phase_arg(val) for val in vals]
                if name.startswith('u2'):
                    if len(phases) != 2: raise TypeError("Invalid specification {}".format(name))
                    gates.append(ZPhase(argset[0],phase=(phases[1]-Fraction(1,2))%2))
                    gates.append(XPhase(argset[0],phase=Fraction(1,2)))
                    gates.append(ZPhase(argset[0],phase=(phases[0]+Fraction(1,2))%2))
                    continue
                else:
                    # See equation (5) of https://arxiv.org/pdf/1707.03429.pdf
                    if len(phases) != 3: raise TypeError("Invalid specification {}".format(name))
                    gates.append(ZPhase(argset[0],phase=phases[2]))
                    gates.append(XPhase(argset[0],phase=Fraction(1,2)))
                    gates.append(ZPhase(argset[0],phase=(phases[0]+1)%2))
                    gates.append(XPhase(argset[0],phase=Fraction(1,2)))
                    gates.append(ZPhase(argset[0],phase=(phases[1]+3)%2))
                    continue
            if name in ("cx","CX","cz","ch"):
                g = qasm_gate_table[name](control=argset[0],target=argset[1]) # type: ignore
                gates.append(g)
                continue
            if name in ("ccx", "ccz"):
                g = qasm_gate_table[name](ctrl1=argset[0],ctrl2=argset[1],target=argset[2]) # type: ignore
                gates.append(g)
                continue
            raise TypeError("Unknown gate name: {}".format(c))
        return gates

    def parse_phase_arg(self, val):
        try:
            phase = float(val)/math.pi
        except ValueError:
            if val.find('pi') == -1: raise TypeError("Invalid specification {}".format(name))
            try:
                val = val.replace('pi', '')
                val = val.replace('*','')
                if val.find('/') != -1:
                    n, d = val.split('/',1)
                    n = n.strip()
                    if not n: n = 1
                    elif n == '-': n = -1
                    else: n = int(n)
                    d = int(d.strip())
                    phase = Fraction(n,d)
                else:
                    val = val.strip()
                    if not val: phase = 1
                    else: phase = float(val)
            except: raise TypeError("Invalid specification {}".format(val))
        phase = Fraction(phase).limit_denominator(100000000)
        return phase

def qasm(s: str) -> Circuit:
    """Parses a string representing a program in QASM, and outputs a `Circuit`."""
    p = QASMParser()
    return p.parse(s, strict=False)

