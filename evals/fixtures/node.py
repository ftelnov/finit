from __future__ import annotations

import tempfile
from pathlib import Path

from fixtures.base import RepoFixture, _git_init, _write_files


def node_express_project(tmp_path: Path | None = None) -> RepoFixture:
    """Minimal Node.js Express project."""
    path = tmp_path or Path(tempfile.mkdtemp(prefix="eval-node-express-"))
    files = {
        "package.json": (
            '{\n'
            '  "name": "demo-api",\n'
            '  "version": "1.0.0",\n'
            '  "main": "src/index.js",\n'
            '  "scripts": {\n'
            '    "start": "node src/index.js",\n'
            '    "test": "jest",\n'
            '    "lint": "eslint src/"\n'
            '  },\n'
            '  "dependencies": {\n'
            '    "express": "^4.19.0"\n'
            '  },\n'
            '  "devDependencies": {\n'
            '    "jest": "^29.7.0",\n'
            '    "supertest": "^7.0.0",\n'
            '    "eslint": "^9.0.0"\n'
            '  }\n'
            '}\n'
        ),
        "src/index.js": (
            "const express = require('express');\n\n"
            "const app = express();\n"
            "const PORT = process.env.PORT || 3000;\n\n"
            "app.get('/', (req, res) => {\n"
            "  res.json({ status: 'ok' });\n"
            "});\n\n"
            "if (require.main === module) {\n"
            "  app.listen(PORT, () => console.log(`Listening on ${PORT}`));\n"
            "}\n\n"
            "module.exports = app;\n"
        ),
        "tests/app.test.js": (
            "const request = require('supertest');\n"
            "const app = require('../src/index');\n\n"
            "describe('GET /', () => {\n"
            "  it('returns 200 with status ok', async () => {\n"
            "    const res = await request(app).get('/');\n"
            "    expect(res.status).toBe(200);\n"
            "    expect(res.body.status).toBe('ok');\n"
            "  });\n"
            "});\n"
        ),
        ".gitignore": "node_modules/\n",
    }
    _write_files(path, files)
    _git_init(path)
    return RepoFixture(path=path, language="javascript", framework="express", files=files)


def node_typescript_project(tmp_path: Path | None = None) -> RepoFixture:
    """TypeScript Express project — tests TS detection and build step."""
    path = tmp_path or Path(tempfile.mkdtemp(prefix="eval-node-ts-"))
    files = {
        "package.json": (
            '{\n'
            '  "name": "ts-api",\n'
            '  "version": "1.0.0",\n'
            '  "scripts": {\n'
            '    "build": "tsc",\n'
            '    "start": "node dist/index.js",\n'
            '    "dev": "ts-node src/index.ts",\n'
            '    "test": "jest --config jest.config.js",\n'
            '    "lint": "eslint src/"\n'
            '  },\n'
            '  "dependencies": {\n'
            '    "express": "^4.19.0"\n'
            '  },\n'
            '  "devDependencies": {\n'
            '    "typescript": "^5.4.0",\n'
            '    "@types/express": "^4.17.0",\n'
            '    "@types/jest": "^29.5.0",\n'
            '    "jest": "^29.7.0",\n'
            '    "ts-jest": "^29.1.0",\n'
            '    "ts-node": "^10.9.0",\n'
            '    "supertest": "^7.0.0",\n'
            '    "@types/supertest": "^6.0.0",\n'
            '    "eslint": "^9.0.0"\n'
            '  }\n'
            '}\n'
        ),
        "tsconfig.json": (
            '{\n'
            '  "compilerOptions": {\n'
            '    "target": "ES2020",\n'
            '    "module": "commonjs",\n'
            '    "outDir": "./dist",\n'
            '    "rootDir": "./src",\n'
            '    "strict": true,\n'
            '    "esModuleInterop": true\n'
            '  },\n'
            '  "include": ["src/**/*"]\n'
            '}\n'
        ),
        "src/index.ts": (
            "import express, { Request, Response } from 'express';\n\n"
            "const app = express();\n"
            "const PORT = process.env.PORT || 3000;\n\n"
            "app.get('/', (req: Request, res: Response) => {\n"
            "  res.json({ status: 'ok' });\n"
            "});\n\n"
            "if (require.main === module) {\n"
            "  app.listen(PORT, () => console.log(`Listening on ${PORT}`));\n"
            "}\n\n"
            "export default app;\n"
        ),
        ".gitignore": "node_modules/\ndist/\n",
    }
    _write_files(path, files)
    _git_init(path)
    return RepoFixture(path=path, language="typescript", framework="express", files=files)


def node_tap_project(tmp_path: Path | None = None) -> RepoFixture:
    """Express project using node-tap (not jest) as test runner.
    Tests that the system detects and follows the actual test framework."""
    path = tmp_path or Path(tempfile.mkdtemp(prefix="eval-node-tap-"))
    files = {
        "package.json": (
            '{\n'
            '  "name": "tap-api",\n'
            '  "version": "1.0.0",\n'
            '  "main": "src/index.js",\n'
            '  "scripts": {\n'
            '    "start": "node src/index.js",\n'
            '    "test": "tap tests/*.test.js"\n'
            '  },\n'
            '  "dependencies": {\n'
            '    "express": "^4.19.0"\n'
            '  },\n'
            '  "devDependencies": {\n'
            '    "tap": "^21.0.0",\n'
            '    "supertest": "^7.0.0"\n'
            '  }\n'
            '}\n'
        ),
        "src/index.js": (
            "const express = require('express');\n\n"
            "const app = express();\n"
            "const PORT = process.env.PORT || 3000;\n\n"
            "app.get('/', (req, res) => {\n"
            "  res.json({ status: 'ok' });\n"
            "});\n\n"
            "if (require.main === module) {\n"
            "  app.listen(PORT, () => console.log(`Listening on ${PORT}`));\n"
            "}\n\n"
            "module.exports = app;\n"
        ),
        "tests/root.test.js": (
            "const tap = require('tap');\n"
            "const request = require('supertest');\n"
            "const app = require('../src/index');\n\n"
            "tap.test('GET / returns 200', async (t) => {\n"
            "  const res = await request(app).get('/');\n"
            "  t.equal(res.status, 200);\n"
            "  t.same(res.body, { status: 'ok' });\n"
            "});\n"
        ),
        ".gitignore": "node_modules/\n",
    }
    _write_files(path, files)
    _git_init(path)
    return RepoFixture(path=path, language="javascript", framework="express", files=files)
