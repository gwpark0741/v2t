# Shared Rules

## Role
This document defines rules shared by all agents in the pipeline.
Every agent must read this first before following its role-specific rules.

## Primary Goal
Implement the specification in a controlled pipeline:
1. set up environment first with `uv`
2. implement by module
3. review before applying broad integration
4. test approved code with real I/O checks
5. report results clearly for the user

## Source of Truth
- The specification document is the highest-priority source.
- Do not invent features not present in the specification.
- When the specification is ambiguous, document the ambiguity and choose the smallest safe implementation.

## Global Working Rules
1. Work inside the `prototype` directory.
2. Prefer the smallest number of files needed.
3. Keep code simple, explicit, and easy to integrate.
4. Reuse existing code whenever practical.
5. Separate modules by responsibility.
6. Explain progress so the user can follow the implementation.
7. Implement and test incrementally, module by module.
8. Avoid major-risk actions without review.
9. Ask for user approval only when security, secrets, destructive actions, or privileged permissions are involved.

## Environment Rules
1. Prepare the environment with `uv` before implementation.
2. Record the commands used for setup.
3. Keep dependencies minimal.
4. Do not add unnecessary packages.

## Documentation Rules
1. Every implementation unit must clearly state:
   - purpose
   - inputs
   - outputs
   - dependency points
2. Record key reviewer-developer discussion points.
3. Keep a concise implementation log and test summary.

## Definition of Done
A task is done only when:
- the requested feature is implemented
- reviewer approval is obtained
- tester validates intended input/output behavior
- a test report is written
