# 🚀 KEY FEATURES OF THIS EXTRACTOR:
# Functions & Classes (with nested ones)
# Standalone code blocks (not in functions)
# Logic patterns (loops, conditionals, try-except)
# Configuration variables
# Comments & docstrings (with context)
# Pipeline steps (identifies workflow)
# Dependencies (categorized by purpose)

import ast
import json
import os
import re
from typing import Dict, List, Any, Optional

class PythonKnowledgeExtractor:
    """Extracts ALL meaningful knowledge from any Python file."""
    
    def __init__(self):
        self.knowledge = {
            'file_info': {},
            'imports': [],
            'functions': [],
            'classes': [],
            'global_code_blocks': [],
            'config_variables': [],
            'comments_and_docstrings': [],
            'pipeline_steps': [],
            'logic_blocks': [],
            'patterns_and_conventions': [],
            'dependencies': [],
            'metadata': {}
        }
    
    def extract(self, filepath: str) -> Dict[str, Any]:
        """Main extraction method."""
        
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')
        
        self.knowledge['file_info'] = {
            'filename': os.path.basename(filepath),
            'path': filepath,
            'size_bytes': os.path.getsize(filepath),
            'total_lines': len(lines)
        }
        
        # Parse AST
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            print(f"Syntax error: {e}")
            # Fallback to line-based extraction
            return self._extract_from_lines(lines)
        
        # Extract all types of knowledge
        self._extract_imports(tree)
        self._extract_functions_and_classes(tree)
        self._extract_global_code(tree, lines)
        self._extract_comments_and_docstrings(lines)
        self._extract_configuration(tree, lines)
        self._extract_logic_patterns(tree, lines)
        self._identify_pipeline_steps(lines)
        self._extract_dependencies(tree)
        
        return self.knowledge
    
    def _extract_imports(self, tree: ast.AST):
        """Extract all imports with categorization."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.knowledge['imports'].append({
                        'module': alias.name,
                        'alias': alias.asname,
                        'type': 'import',
                        'line': node.lineno
                    })
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    full_name = f"{module}.{alias.name}" if module else alias.name
                    self.knowledge['imports'].append({
                        'module': module,
                        'name': alias.name,
                        'alias': alias.asname,
                        'full_name': full_name,
                        'type': 'from_import',
                        'line': node.lineno
                    })
    
    def _extract_functions_and_classes(self, tree: ast.AST):
        """Extract functions, classes, and their methods."""
        
        def extract_function(func_node: ast.FunctionDef, depth: int = 0) -> Dict:
            """Extract detailed function information."""
            func_info = {
                'name': func_node.name,
                'docstring': ast.get_docstring(func_node),
                'args': [],
                'decorators': [self._unparse(decorator) for decorator in func_node.decorator_list],
                'line_start': func_node.lineno,
                'line_end': func_node.end_lineno,
                'depth': depth,
                'type': 'function'
            }
            
            # Extract arguments
            for arg in func_node.args.args:
                func_info['args'].append({
                    'name': arg.arg,
                    'annotation': self._unparse(arg.annotation) if arg.annotation else None
                })
            
            # Extract function body structure
            func_info['body_structure'] = self._extract_body_structure(func_node.body)
            
            return func_info
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                self.knowledge['functions'].append(extract_function(node))
            
            elif isinstance(node, ast.ClassDef):
                class_info = {
                    'name': node.name,
                    'docstring': ast.get_docstring(node),
                    'line_start': node.lineno,
                    'line_end': node.end_lineno,
                    'bases': [self._unparse(base) for base in node.bases],
                    'decorators': [self._unparse(decorator) for decorator in node.decorator_list],
                    'methods': []
                }
                
                # Extract methods
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        class_info['methods'].append(extract_function(item, depth=1))
                
                self.knowledge['classes'].append(class_info)
    
    def _extract_global_code(self, tree: ast.AST, lines: List[str]):
        """Extract standalone code blocks (not in functions/classes)."""
        code_blocks = []
        current_block = []
        block_start = None
        
        # Find top-level nodes that aren't imports, functions, or classes
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
                # Save previous block if exists
                if current_block and block_start:
                    if len(current_block) > 2:  # Only meaningful blocks
                        code_blocks.append({
                            'lines': block_start,
                            'content': '\n'.join(current_block),
                            'type': self._identify_block_type(current_block),
                            'node_type': type(node).__name__
                        })
                current_block = []
                block_start = None
                continue
            
            # Extract code for this node
            node_lines = lines[node.lineno-1:node.end_lineno]
            if not block_start:
                block_start = node.lineno
            
            current_block.extend(node_lines)
        
        # Add the last block
        if current_block and block_start and len(current_block) > 2:
            code_blocks.append({
                'lines': f"{block_start}-{block_start + len(current_block) - 1}",
                'content': '\n'.join(current_block),
                'type': self._identify_block_type(current_block),
                'node_type': 'standalone_code'
            })
        
        self.knowledge['global_code_blocks'] = code_blocks
    
    def _extract_comments_and_docstrings(self, lines: List[str]):
        """Extract meaningful comments and docstrings."""
        comments = []
        
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            
            # Extract single-line comments
            if stripped.startswith('#'):
                comment_text = stripped[1:].strip()
                if len(comment_text) > 5 and not comment_text.startswith('!'):  # Meaningful comments
                    # Check if it's explaining something
                    if any(keyword in comment_text.lower() for keyword in 
                          ['why', 'because', 'reason', 'note', 'important', 'todo', 'fixme']):
                        comments.append({
                            'line': i,
                            'text': comment_text,
                            'type': 'explanation',
                            'context': self._get_context(lines, i, 2)
                        })
                    else:
                        comments.append({
                            'line': i,
                            'text': comment_text,
                            'type': 'comment',
                            'context': self._get_context(lines, i, 2)
                        })
            
            # Extract multi-line comments (docstring style)
            elif stripped.startswith(('"""', "'''")):
                # Find the end of the docstring
                end_idx = i - 1
                while end_idx < len(lines) and not (stripped.endswith(('"""', "'''")) and len(stripped) > 3):
                    end_idx += 1
                    if end_idx < len(lines):
                        stripped += lines[end_idx].strip()
                
                docstring = '\n'.join(lines[i-1:end_idx+1])
                comments.append({
                    'line': f"{i}-{end_idx+1}",
                    'text': docstring,
                    'type': 'docstring',
                    'context': self._get_context(lines, i, 2)
                })
        
        self.knowledge['comments_and_docstrings'] = comments
    
    def _extract_configuration(self, tree: ast.AST, lines: List[str]):
        """Extract configuration variables and settings."""
        config_vars = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        var_name = target.id
                        
                        # Check if it looks like a configuration variable
                        is_config = (
                            var_name.isupper() or  # UPPER_CASE
                            'path' in var_name.lower() or
                            'dir' in var_name.lower() or
                            'file' in var_name.lower() or
                            'config' in var_name.lower() or
                            'param' in var_name.lower() or
                            'setting' in var_name.lower()
                        )
                        
                        if is_config:
                            try:
                                value = self._unparse(node.value)
                            except:
                                value = str(ast.dump(node.value))
                            
                            config_vars.append({
                                'name': var_name,
                                'value': value[:200] + "..." if len(value) > 200 else value,
                                'line': node.lineno,
                                'type': 'configuration'
                            })
        
        self.knowledge['config_variables'] = config_vars
    
    def _extract_logic_patterns(self, tree: ast.AST, lines: List[str]):
        """Extract important logic patterns (loops, conditionals)."""
        logic_blocks = []
        
        def extract_logic(node: ast.AST, depth: int = 0):
            node_type = type(node).__name__
            
            if node_type in ['For', 'While', 'If', 'Try', 'With']:
                # Get the code block
                start_line = node.lineno
                end_line = node.end_lineno
                code_lines = lines[start_line-1:end_line]
                
                logic_blocks.append({
                    'type': node_type.lower(),
                    'line_start': start_line,
                    'line_end': end_line,
                    'depth': depth,
                    'code': '\n'.join(code_lines[:10]),  # First 10 lines
                    'purpose': self._infer_purpose(code_lines)
                })
            
            # Recursively check children
            for child in ast.iter_child_nodes(node):
                extract_logic(child, depth + 1)
        
        extract_logic(tree)
        self.knowledge['logic_blocks'] = logic_blocks
    
    def _identify_pipeline_steps(self, lines: List[str]):
        """Identify pipeline/steps in the code."""
        steps = []
        current_step = None
        
        for i, line in enumerate(lines, 1):
            stripped = line.strip().lower()
            
            # Check for step indicators
            step_indicators = [
                '# step', '# phase', '# stage', '# process',
                '# train', '# test', '# validate', '# preprocess',
                '# load', '# save', '# evaluate', '# predict'
            ]
            
            for indicator in step_indicators:
                if stripped.startswith(indicator):
                    if current_step:
                        steps.append(current_step)
                    
                    current_step = {
                        'name': line.strip()[1:].strip(),
                        'line_start': i,
                        'lines': []
                    }
                    break
            
            # Add to current step
            if current_step and not stripped.startswith('#'):
                current_step['lines'].append(line)
        
        # Add the last step
        if current_step:
            steps.append(current_step)
        
        self.knowledge['pipeline_steps'] = steps
    
    def _extract_dependencies(self, tree: ast.AST):
        """Extract dependency information."""
        dependencies = []
        
        for import_item in self.knowledge['imports']:
            module = import_item.get('module') or import_item.get('full_name', '')
            if module:
                # Categorize dependencies
                category = self._categorize_dependency(module)
                dependencies.append({
                    'module': module,
                    'category': category,
                    'purpose': self._get_dependency_purpose(module)
                })
        
        # Remove duplicates
        unique_deps = []
        seen = set()
        for dep in dependencies:
            key = dep['module']
            if key not in seen:
                seen.add(key)
                unique_deps.append(dep)
        
        self.knowledge['dependencies'] = unique_deps
    
    def _extract_from_lines(self, lines: List[str]) -> Dict[str, Any]:
        """Fallback extraction when AST parsing fails."""
        # Basic line-based extraction
        self.knowledge['file_info']['parse_method'] = 'line_based'
        
        # Extract comments
        self._extract_comments_and_docstrings(lines)
        
        # Try to identify functions by pattern
        functions = []
        for i, line in enumerate(lines, 1):
            if re.match(r'^\s*def\s+\w+\s*\(', line):
                # Found a function
                func_name = re.search(r'def\s+(\w+)', line).group(1)
                functions.append({
                    'name': func_name,
                    'line_start': i,
                    'type': 'function'
                })
        
        self.knowledge['functions'] = functions
        
        return self.knowledge
    
    # Helper methods
    def _unparse(self, node: ast.AST) -> str:
        """Safely unparse AST node to string."""
        try:
            if hasattr(ast, 'unparse'):
                return ast.unparse(node)
            else:
                return str(node)
        except:
            return str(type(node))
    
    def _get_context(self, lines: List[str], line_num: int, context_lines: int = 3) -> str:
        """Get context around a line."""
        start = max(0, line_num - context_lines - 1)
        end = min(len(lines), line_num + context_lines)
        return '\n'.join(lines[start:end])
    
    def _identify_block_type(self, block_lines: List[str]) -> str:
        """Identify the type of code block."""
        block_text = '\n'.join(block_lines).lower()
        
        if any(x in block_text for x in ['model.fit', 'model.train', 'training']):
            return 'training'
        elif any(x in block_text for x in ['preprocess', 'clean', 'transform']):
            return 'preprocessing'
        elif any(x in block_text for x in ['evaluat', 'score', 'metric']):
            return 'evaluation'
        elif any(x in block_text for x in ['visualiz', 'plot', 'chart']):
            return 'visualization'
        elif any(x in block_text for x in ['save', 'load', 'export']):
            return 'io_operation'
        elif any(x in block_text for x in ['config', 'param', 'setting']):
            return 'configuration'
        else:
            return 'execution'
    
    def _infer_purpose(self, code_lines: List[str]) -> str:
        """Infer the purpose of a code block."""
        code_text = '\n'.join(code_lines).lower()
        
        purposes = {
            'iteration': ['for ', 'while ', 'loop'],
            'condition': ['if ', 'elif ', 'else'],
            'error_handling': ['try:', 'except', 'finally'],
            'resource_management': ['with ', 'open(', 'close('],
            'data_processing': ['process', 'transform', 'filter'],
            'model_operation': ['predict', 'fit', 'train', 'score']
        }
        
        for purpose, keywords in purposes.items():
            if any(keyword in code_text for keyword in keywords):
                return purpose
        
        return 'general_logic'
    
    def _categorize_dependency(self, module: str) -> str:
        """Categorize a dependency."""
        module_lower = module.lower()
        
        categories = {
            'data_processing': ['pandas', 'numpy', 'polars'],
            'ml_framework': ['sklearn', 'scikit-learn', 'tensorflow', 'keras', 'pytorch'],
            'visualization': ['matplotlib', 'seaborn', 'plotly'],
            'utilities': ['os', 'sys', 'json', 'pickle', 'joblib'],
            'specialized': ['imblearn', 'xgboost', 'lightgbm']
        }
        
        for category, keywords in categories.items():
            if any(keyword in module_lower for keyword in keywords):
                return category
        
        return 'other'
    
    def _get_dependency_purpose(self, module: str) -> str:
        """Get the purpose of a dependency."""
        purposes = {
            'pandas': 'Data manipulation and analysis',
            'numpy': 'Numerical computations',
            'sklearn': 'Machine learning algorithms',
            'tensorflow': 'Deep learning framework',
            'keras': 'Neural network API',
            'matplotlib': 'Data visualization',
            'seaborn': 'Statistical data visualization',
            'xgboost': 'Gradient boosting framework',
            'imblearn': 'Imbalanced learning techniques'
        }
        
        return purposes.get(module.lower().split('.')[0], 'General purpose')
    
    def _extract_body_structure(self, body_nodes: List[ast.AST]) -> List[Dict]:
        """Extract structure of function/class body."""
        structure = []
        
        for node in body_nodes:
            node_type = type(node).__name__
            if node_type in ['Assign', 'AugAssign', 'AnnAssign']:
                structure.append({'type': 'assignment', 'detail': node_type})
            elif node_type in ['For', 'While']:
                structure.append({'type': 'loop', 'detail': node_type})
            elif node_type in ['If', 'IfExp']:
                structure.append({'type': 'conditional', 'detail': node_type})
            elif node_type == 'Return':
                structure.append({'type': 'return', 'detail': node_type})
            elif node_type == 'Expr':
                structure.append({'type': 'expression', 'detail': node_type})
        
        return structure
    
    def save_knowledge(self, output_path: str):
        """Save extracted knowledge to JSON file."""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.knowledge, f, indent=2, default=str)
        
        print(f"✅ Knowledge saved to {output_path}")
        
        # Also save a summary
        self._save_summary(output_path.replace('.json', '_summary.txt'))
    
    def _save_summary(self, summary_path: str):
        """Save a human-readable summary."""
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(f"📊 KNOWLEDGE EXTRACTION SUMMARY\n")
            f.write(f"{'='*60}\n")
            f.write(f"File: {self.knowledge['file_info']['filename']}\n")
            f.write(f"Path: {self.knowledge['file_info']['path']}\n")
            f.write(f"Lines: {self.knowledge['file_info']['total_lines']}\n\n")
            
            f.write(f"📦 DEPENDENCIES ({len(self.knowledge['dependencies'])}):\n")
            for dep in self.knowledge['dependencies'][:10]:
                f.write(f"  • {dep['module']} ({dep['category']}) - {dep['purpose']}\n")
            
            f.write(f"\n🔧 FUNCTIONS ({len(self.knowledge['functions'])}):\n")
            for func in self.knowledge['functions'][:5]:
                f.write(f"  • {func['name']}() - lines {func['line_start']}-{func['line_end']}\n")
            
            f.write(f"\n🏗️  CLASSES ({len(self.knowledge['classes'])}):\n")
            for cls in self.knowledge['classes'][:5]:
                f.write(f"  • {cls['name']} - {len(cls['methods'])} methods\n")
            
            f.write(f"\n📝 CODE BLOCKS ({len(self.knowledge['global_code_blocks'])}):\n")
            for block in self.knowledge['global_code_blocks'][:5]:
                f.write(f"  • {block['type']} - {block['lines']}\n")
            
            f.write(f"\n💡 LOGIC PATTERNS ({len(self.knowledge['logic_blocks'])}):\n")
            for logic in self.knowledge['logic_blocks'][:5]:
                f.write(f"  • {logic['type']} - lines {logic['line_start']}-{logic['line_end']}\n")
            
            f.write(f"\n🔧 PIPELINE STEPS ({len(self.knowledge['pipeline_steps'])}):\n")
            for step in self.knowledge['pipeline_steps'][:5]:
                f.write(f"  • {step['name'][:50]}...\n")

# USAGE
if __name__ == "__main__":
    # Initialize extractor
    extractor = PythonKnowledgeExtractor()
    
    # File to process
    python_file = "D:/Infinium/Raveeha - Domain(Health)/AI-CardioCare (TestRepo)/Model Multi Class/Heart_Disease_Prediction.py"  # Update this path
    
    if os.path.exists(python_file):
        print(f"🔍 Processing: {python_file}")
        
        # Extract knowledge
        knowledge = extractor.extract(python_file)
        
        # Save results
        extractor.save_knowledge("complete_python_knowledge.json")
        
        # Print quick stats
        print(f"\n📊 EXTRACTION STATISTICS:")
        print(f"  • Dependencies: {len(knowledge['dependencies'])}")
        print(f"  • Functions: {len(knowledge['functions'])}")
        print(f"  • Classes: {len(knowledge['classes'])}")
        print(f"  • Code Blocks: {len(knowledge['global_code_blocks'])}")
        print(f"  • Logic Patterns: {len(knowledge['logic_blocks'])}")
        print(f"  • Comments: {len(knowledge['comments_and_docstrings'])}")
        print(f"  • Pipeline Steps: {len(knowledge['pipeline_steps'])}")
        
    else:
        print(f"❌ File not found: {python_file}")