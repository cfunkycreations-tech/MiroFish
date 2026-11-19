"""
让 OASIS 在没有 torch / sentence-transformers / scikit-learn 时也能导入。

背景：oasis/social_platform/recsys.py 在模块顶层就 import torch、sentence_transformers、
sklearn，因此即使只想用随机/热度推荐（reddit、random），也必须安装 torch（约 1.2 GB）。
MiroFish 只有 twitter 平台的个性化推荐真正用到它们。

本脚本把这些顶层导入改成惰性导入，并在缺少依赖时给出清晰提示。
"""

import io
import os
import sys

TARGET_REL = os.path.join('oasis', 'social_platform', 'recsys.py')

# camel-oasis 0.2.5 在 METADATA 里硬性声明了这些依赖，但 MiroFish 的流程并不会用到：
#   sentence-transformers / torch / scipy / scikit-learn -> 仅 twitter 个性化推荐
#   cairocffi -> 只在可视化脚本里用
#   neo4j -> AgentGraph 默认使用 igraph 后端
# 去掉声明后，pip/uv 就不会再拉取约 1.4 GB 的依赖。
STRIP_DEPS = ('torch', 'transformers', 'sentence-transformers', 'sentence_transformers',
              'scikit-learn', 'scipy', 'cairocffi', 'neo4j',
              # 开发期依赖，运行不需要
              'pre-commit', 'pytest', 'pytest-asyncio')


def strip_metadata(site_packages):
    """删除 camel_oasis 的 METADATA 里不必要的 Requires-Dist，返回删除条数。"""
    import glob
    removed = 0
    for meta in glob.glob(os.path.join(site_packages, 'camel_oasis-*.dist-info', 'METADATA')):
        with io.open(meta, encoding='utf-8') as f:
            lines = f.readlines()
        kept = []
        for line in lines:
            if line.startswith('Requires-Dist:'):
                # 形如 "sentence-transformers (==3.0.0)" 或 "torch>=2.0 ; extra == 'x'"
                name = line.split(':', 1)[1].strip()
                name = name.split(';')[0].strip()
                name = name.split('(')[0].strip()
                name = name.split('[')[0].strip()
                for sep in ('==', '>=', '<=', '~=', '!=', '>', '<', ' @'):
                    name = name.split(sep)[0].strip()
                name = name.lower()
                if name in STRIP_DEPS:
                    removed += 1
                    continue
            kept.append(line)
        with io.open(meta, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(kept)
    return removed
TARGET_REL_POSTS = os.path.join('oasis', 'social_platform', 'process_recsys_posts.py')

# process_recsys_posts.py 的顶层依赖（用于向量生成，仅 twitter 个性化推荐需要）
POSTS_HEADER = '''import torch
from camel.embeddings import OpenAIEmbedding
from camel.types import EmbeddingModelType
from transformers import AutoModel, AutoTokenizer
'''

POSTS_REPLACEMENT = '''from camel.embeddings import OpenAIEmbedding
from camel.types import EmbeddingModelType


# ---------------------------------------------------------------------------
# 轻量模式补丁：torch / transformers 改为按需导入
# ---------------------------------------------------------------------------
class _MissingHeavyDeps(RuntimeError):
    pass


def _require_torch():
    try:
        import torch
        return torch
    except ImportError as exc:
        raise _MissingHeavyDeps(
            '该推荐算法需要 torch。请安装: pip install torch'
        ) from exc


def _require_transformers():
    try:
        from transformers import AutoModel, AutoTokenizer
        return AutoModel, AutoTokenizer
    except ImportError as exc:
        raise _MissingHeavyDeps(
            '该推荐算法需要 transformers。请安装: pip install transformers'
        ) from exc


AutoModel = object   # 仅用于类型注解
AutoTokenizer = object
'''

HEADER = '''import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
'''

REPLACEMENT = '''# ---------------------------------------------------------------------------
# 轻量模式补丁：torch / sentence-transformers / scikit-learn 改为按需导入
# （由 MiroFish 的安装脚本自动应用；只有 twitter 个性化推荐真正需要它们）
# ---------------------------------------------------------------------------
import numpy as np


class _MissingHeavyDeps(RuntimeError):
    pass


def _require_torch():
    try:
        import torch
        return torch
    except ImportError as exc:
        raise _MissingHeavyDeps(
            'OASIS 的个性化推荐需要 torch。请安装: pip install torch '
            '(或改用 reddit / random 平台)'
        ) from exc


def _require_sentence_transformers():
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer
    except ImportError as exc:
        raise _MissingHeavyDeps(
            'OASIS 的个性化推荐需要 sentence-transformers。'
            '请安装: pip install sentence-transformers'
        ) from exc


def _require_sklearn():
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        return TfidfVectorizer, cosine_similarity
    except ImportError as exc:
        raise _MissingHeavyDeps(
            'OASIS 的个性化推荐需要 scikit-learn。请安装: pip install scikit-learn'
        ) from exc


class _LazyModule(object):
    """以属性访问方式按需导入 torch。"""

    def __getattr__(self, name):
        return getattr(_require_torch(), name)


torch = _LazyModule()
'''

DEVICE_LINE = 'device = torch.device("cuda" if torch.cuda.is_available() else "cpu")'
DEVICE_REPLACEMENT = ('device = _require_torch().device('
                      '"cuda" if _require_torch().cuda.is_available() else "cpu")')


def patch_file(path):
    """返回 patched / already / missing-header。"""
    with io.open(path, encoding='utf-8') as f:
        source = f.read()

    if '_MissingHeavyDeps' in source:
        return 'already'

    heavy = ('import torch\n'
             'from sentence_transformers import SentenceTransformer\n'
             'from sklearn.feature_extraction.text import TfidfVectorizer\n'
             'from sklearn.metrics.pairwise import cosine_similarity\n')

    if HEADER in source:
        source = source.replace(HEADER, REPLACEMENT, 1)
    elif heavy in source:
        # 顺序为 numpy -> torch -> sentence_transformers -> sklearn
        source = source.replace('import numpy as np\n' + heavy,
                                REPLACEMENT, 1)
    else:
        # 逐行删除，保留 numpy
        lines = source.splitlines(keepends=True)
        drop_prefixes = ('import torch', 'from sentence_transformers',
                         'from sklearn.feature_extraction.text',
                         'from sklearn.metrics.pairwise')
        kept = [ln for ln in lines if not ln.startswith(drop_prefixes)]
        if len(kept) == len(lines):
            return 'missing-header'
        source = ''.join(kept)
        # 在 numpy 之后插入补丁代码
        marker = 'import numpy as np\n'
        if marker not in source:
            return 'missing-header'
        source = source.replace(marker, marker + REPLACEMENT.split('import numpy as np\n', 1)[1], 1)
    # 只替换模块级那一处（函数内的 device 保留原样）
    source = source.replace(
        DEVICE_LINE,
        '# 设备选择同样延迟（只有个性化推荐需要）\n'
        'device = None',
        1,
    )
    source = source.replace(
        "device = 'cuda' if torch.cuda.is_available() else 'cpu'",
        'device = None',
        1,
    )

    # 模块级的 TfidfVectorizer 实例化 + 局部引用改名，避免导入期就需要 sklearn
    source = source.replace(
        'tfidf_vectorizer = TfidfVectorizer()',
        '# TfidfVectorizer 延迟到真正使用（reddit 平台）时再创建\n'
        'tfidf_vectorizer = None',
    )
    source = source.replace(
        'cosine_similarities = cosine_similarity(user_vector, posts_vector)',
        'cosine_similarities = _require_sklearn()[1](user_vector, posts_vector)',
    )

    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(source)
    return 'patched'


def patch_posts_file(path):
    """处理 process_recsys_posts.py（torch 装饰器 + 顶层 transformers 导入）。"""
    with io.open(path, encoding='utf-8') as f:
        source = f.read()

    if '_MissingHeavyDeps' in source:
        return 'already'

    if POSTS_HEADER not in source:
        return 'missing-header'

    source = source.replace(POSTS_HEADER, POSTS_REPLACEMENT, 1)

    # @torch.no_grad() 是装饰器，需要在导入期就存在 -> 改为运行期惰性包装
    # 装饰器会在导入期求值，改为导入期惰性、运行期真实校验
    source = source.replace(
        '@torch.no_grad()\ndef process_batch(',
        '@_lazy_no_grad\ndef process_batch(',
    )
    source = source.replace(
        'AutoModel = object   # 仅用于类型注解',
        '''def _lazy_no_grad(func):
    """torch.no_grad() 的惰性版本：真正的依赖检查延迟到函数调用时。"""
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _require_torch().no_grad():
            return func(*args, **kwargs)

    return wrapper


AutoModel = object   # 仅用于类型注解''')
    source = source.replace(
        '    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")',
        '    torch = _require_torch()\n'
        '    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")',
    )
    source = source.replace(
        'def generate_post_vector(model: AutoModel, tokenizer: AutoTokenizer, texts,\n                         batch_size):',
        'def generate_post_vector(model: AutoModel, tokenizer: AutoTokenizer, texts,\n                         batch_size):\n    torch = _require_torch()',
    )

    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(source)
    return 'patched'


def strip_all_metadata(site_packages):
    """瘦身模式：同时清掉 camel_oasis 与 camel_ai 里不需要的依赖声明。"""
    total = strip_metadata(site_packages)
    print(f'[patch_oasis_recsys] 移除 {total} 条不必要的依赖声明（sentence-transformers / torch 等）')
    return total


def patch_site_packages(site_packages):
    target = os.path.join(site_packages, TARGET_REL)
    posts = os.path.join(site_packages, TARGET_REL_POSTS)
    if not os.path.exists(target):
        return 'not-found', target
    result = patch_file(target)
    if os.path.exists(posts):
        posts_result = patch_posts_file(posts)
        print(f'[patch_oasis_recsys] process_recsys_posts: {posts_result}')
    return result, target


def main(argv):
    # 支持 "目录 --slim" 与 "--slim 目录" 两种顺序
    args = [a for a in argv[1:] if not a.startswith('--')]
    if args:
        target = args[0]
        if os.path.isdir(target):
            result, target = patch_site_packages(target)
        else:
            result = patch_file(target) if os.path.exists(target) else 'not-found'
    else:
        try:
            import oasis
        except ImportError:
            print('用法: python patch_oasis_recsys.py <site-packages 目录>')
            return 2
        target = os.path.dirname(oasis.__file__)
        result, target = patch_site_packages(os.path.dirname(target))

    print(f'[patch_oasis_recsys] {result}: {target}')

    slim = '--slim' in sys.argv or '--strip-metadata' in sys.argv
    if slim:
        # target 可能是 recsys.py，也可能是 site-packages 目录
        base = target if os.path.isdir(target) else os.path.dirname(target)
        if os.path.basename(base) == 'social_platform':
            base = os.path.dirname(os.path.dirname(base))
        strip_all_metadata(base)
        return 0

    return 0 if result in ('patched', 'already') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
