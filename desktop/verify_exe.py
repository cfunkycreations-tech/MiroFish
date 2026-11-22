"""
Verify that a cross-compiled MiroFish.exe meets the Windows desktop program requirements.

When cross-compiling a Windows program on Linux, the usual mistakes are:
  * a wrong subsystem (a GUI program built as a console program pops up a black window on double-click)
  * the icon / version info never made it into the resources section
  * a dependency on a non-system DLL (the target machine reports "xxx.dll not found")

Usage:
    python desktop/verify_exe.py build/MiroFish.exe
"""

import struct
import sys

system_dlls = {
    'kernel32.dll', 'user32.dll', 'shell32.dll', 'ole32.dll', 'oleaut32.dll',
    'gdi32.dll', 'advapi32.dll', 'comdlg32.dll', 'comctl32.dll', 'shlwapi.dll',
    'ntdll.dll', 'msvcrt.dll', 'ucrtbase.dll', 'ws2_32.dll', 'dbghelp.dll',
    'api-ms-win-crt-heap-l1-1-0.dll', 'api-ms-win-crt-locale-l1-1-0.dll',
    'api-ms-win-crt-private-l1-1-0.dll', 'api-ms-win-crt-runtime-l1-1-0.dll',
    'api-ms-win-crt-stdio-l1-1-0.dll', 'api-ms-win-crt-string-l1-1-0.dll',
    'api-ms-win-crt-math-l1-1-0.dll', 'api-ms-win-crt-environment-l1-1-0.dll',
    'api-ms-win-crt-convert-l1-1-0.dll', 'api-ms-win-crt-utility-l1-1-0.dll',
    'api-ms-win-crt-time-l1-1-0.dll', 'api-ms-win-crt-filesystem-l1-1-0.dll',
}

RESOURCE_ICON = 3
RESOURCE_GROUP_ICON = 14
RESOURCE_VERSION = 16
RESOURCE_MANIFEST = 24


def check(path):
    problems = []
    with open(path, 'rb') as f:
        data = f.read()

    if data[:2] != b'MZ':
        return ['not a valid PE file']

    pe_offset = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe_offset:pe_offset + 4] != b'PE\0\0':
        return ['the PE header is corrupt']
    machine = struct.unpack_from('<H', data, pe_offset + 4)[0]
    if machine != 0x8664:
        problems.append(f'not an x64 program (machine=0x{machine:04x})')

    optional_off = pe_offset + 24
    magic = struct.unpack_from('<H', data, optional_off)[0]
    subsystem = struct.unpack_from('<H', data, optional_off + 68)[0]
    if subsystem != 2:
        problems.append(f'the subsystem is not GUI (subsystem={subsystem}); a console window would pop up on double-click')

    # Section table
    num_sections = struct.unpack_from('<H', data, pe_offset + 6)[0]
    size_optional = struct.unpack_from('<H', data, pe_offset + 20)[0]
    sections_off = optional_off + size_optional
    sections = []
    for i in range(num_sections):
        off = sections_off + i * 40
        name = data[off:off + 8].rstrip(b'\0').decode('latin-1')
        vaddr, vsize = struct.unpack_from('<II', data, off + 12)
        raw_size, raw_ptr = struct.unpack_from('<II', data, off + 16)
        sections.append((name, vaddr, vsize, raw_ptr, raw_size))

    def rva_to_off(rva):
        for _, vaddr, vsize, raw_ptr, raw_size in sections:
            if vaddr <= rva < vaddr + max(vsize, raw_size):
                return raw_ptr + (rva - vaddr)
        return None

    # Data directories: the import table (1) and the resource table (2)
    dirs_off = optional_off + (112 if magic == 0x20b else 96)
    import_rva, import_size = struct.unpack_from('<II', data, dirs_off + 8)
    res_rva, res_size = struct.unpack_from('<II', data, dirs_off + 16)

    dlls = []
    off = rva_to_off(import_rva)
    if off is None:
        problems.append('there is no import table')
    else:
        i = off
        while True:
            desc = data[i:i + 20]
            if len(desc) < 20 or desc == b'\0' * 20:
                break
            name_rva = struct.unpack_from('<I', desc, 12)[0]
            name_off = rva_to_off(name_rva)
            if name_off is None:
                break
            end = data.index(b'\0', name_off)
            dlls.append(data[name_off:end].decode('latin-1'))
            i += 20
        for dll in dlls:
            if dll.lower() not in system_dlls and not dll.lower().startswith('api-ms-win'):
                problems.append(f'depends on a non-system DLL: {dll} (the target machine may not have it)')

    ids = set()
    if res_rva and res_size:
        root = rva_to_off(res_rva)
        named, ids_count = struct.unpack_from('<HH', data, root + 12)
        for i in range(named + ids_count):
            entry = root + 16 + i * 8
            name_field, offset_field = struct.unpack_from('<II', data, entry)
            if name_field & 0x80000000:
                continue
            ids.add(name_field)
    else:
        problems.append('there is no resource section (the icon / version info is missing)')

    for res, label in ((RESOURCE_ICON, 'icon'), (RESOURCE_GROUP_ICON, 'icon group'),
                       (RESOURCE_VERSION, 'version info'), (RESOURCE_MANIFEST, 'manifest')):
        if res not in ids:
            problems.append(f'missing resource: {label} (id={res})')

    print(f'File     : {path} ({len(data) / 1024:.0f} KB)')
    print(f'Arch     : x64 (0x{machine:04x})')
    print(f'Subsystem: {"GUI" if subsystem == 2 else subsystem}')
    print(f'Imports  : {", ".join(dlls)}')
    print(f'Resources: {sorted(ids)}')
    return problems


def main():
    if len(sys.argv) < 2:
        print('Usage: python desktop/verify_exe.py <MiroFish.exe>')
        return 2
    problems = check(sys.argv[1])
    if problems:
        print('\nX Verification failed:')
        for p in problems:
            print('  -', p)
        return 1
    print('\nOK Verification passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
