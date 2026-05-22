import * as fs from "fs";
import * as path from "path";
import { execFileSync } from "child_process";
import { aws_lambda as lambda } from "aws-cdk-lib";

const backendDir = path.join(__dirname, "..", "..", "backend");
const runtimeRequirements = path.join(backendDir, "requirements-lambda.txt");

function prunePythonArtifacts(dir: string): void {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__pycache__") {
        fs.rmSync(fullPath, { recursive: true, force: true });
        continue;
      }
      prunePythonArtifacts(fullPath);
      continue;
    }
    if (entry.name.endsWith(".pyc")) {
      fs.rmSync(fullPath, { force: true });
    }
  }
}

function copyBackendSources(outputDir: string): void {
  for (const folder of ["chatroom_api", "tick_loop"]) {
    fs.cpSync(path.join(backendDir, folder), path.join(outputDir, folder), {
      recursive: true,
    });
  }
  prunePythonArtifacts(outputDir);
}

function tryLocalBundle(outputDir: string): boolean {
  try {
    execFileSync(
      "python3",
      ["-m", "pip", "install", "-r", runtimeRequirements, "-t", outputDir, "--no-compile"],
      { stdio: "inherit" },
    );
    copyBackendSources(outputDir);
    return true;
  } catch {
    return false;
  }
}

export function backendPythonCode(): lambda.Code {
  return lambda.Code.fromAsset(backendDir, {
    bundling: {
      image: lambda.Runtime.PYTHON_3_12.bundlingImage,
      local: { tryBundle: tryLocalBundle },
      command: [
        "bash",
        "-lc",
        [
          "pip install -r requirements-lambda.txt -t /asset-output --no-compile",
          "cp -r chatroom_api /asset-output/chatroom_api",
          "cp -r tick_loop /asset-output/tick_loop",
          "find /asset-output -name '__pycache__' -type d -prune -exec rm -rf {} +",
          "find /asset-output -name '*.pyc' -delete",
        ].join(" && "),
      ],
    },
  });
}
