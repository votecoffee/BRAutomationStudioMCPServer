"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

const maxBytes = 16 * 1024 * 1024;

function decode(input, output, codecRoot) {
    if (!input || !output || !codecRoot || path.extname(input).toLowerCase() !== ".lpj"
        || path.extname(output).toLowerCase() !== ".json") {
        throw new Error("Usage: node decode-launchpad.cjs <source.lpj> <readback.json> <zipson package directory>");
    }
    const codecDirectory = path.resolve(codecRoot);
    const manifest = JSON.parse(fs.readFileSync(path.join(codecDirectory, "package.json"), "utf8"));
    if (manifest.name !== "zipson" || manifest.version !== "0.2.12") {
        throw new Error("Select the installed zipson 0.2.12 package directory");
    }
    const { parse } = require(path.join(codecDirectory, "lib", "index.js"));
    const fd = fs.openSync(input, "r");
    let raw;
    try {
        const stat = fs.fstatSync(fd);
        if (!stat.isFile() || stat.size === 0 || stat.size > maxBytes) {
            throw new Error("Select a nonempty LaunchPad file up to 16 MiB");
        }
        raw = fs.readFileSync(fd);
    } finally {
        fs.closeSync(fd);
    }
    const source = new TextDecoder("utf-8", { fatal: true }).decode(raw);
    if (raw.length > maxBytes || !source.startsWith("{") || !source.endsWith("}")) {
        throw new Error("Expected a saved LaunchPad Zipson object");
    }
    const decoded = parse(source);
    if (!decoded || Array.isArray(decoded) || typeof decoded !== "object") {
        throw new Error("Expected a decoded LaunchPad object");
    }
    const receipt = {
        schemaVersion: 1,
        source: {
            fileName: path.basename(input),
            sha256: createHash("sha256").update(raw).digest("hex"),
        },
        codec: { name: manifest.name, version: manifest.version },
        decoded,
    };
    const encoded = JSON.stringify(receipt, null, 2) + "\n";
    if (Buffer.byteLength(encoded) > maxBytes) {
        throw new Error("Decoded LaunchPad readback exceeds 16 MiB");
    }
    fs.writeFileSync(output, encoded, { encoding: "utf8", flag: "wx" });
}

try {
    if (process.argv.length !== 5) {
        throw new Error("Usage: node decode-launchpad.cjs <source.lpj> <readback.json> <zipson package directory>");
    }
    decode(...process.argv.slice(2));
} catch (error) {
    process.stderr.write(error.message + "\n");
    process.exitCode = 1;
}
