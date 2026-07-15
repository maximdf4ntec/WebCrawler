---
name: pydantic-domain-variants
description: >
  Enforces the one-class-per-variant pattern for all domain concepts in this project. 
  Activate when adding or reviewing any class that inherits from Rule, Parser, Exporter, 
  or any other abstract domain base. Prevents procedural "bag-of-functions" antipattern.
---
# Pydantic Domain-Variants Skill

## Problem this prevents
A domain base class exists with typed fields and an
abstract method.  Instead of subclassing it, a developer writes
a collection of standalone functions that manually build and return base-class instances.  This is the **procedural bag-of-functions**
antipattern and defeats the purpose of the class hierarchy.

## Rule: one class per variant (non-negotiable)
Every distinct variant of a domain concept **must** be its own Pydantic model class that:

1. **Extends the correct base** 
2. **Declares typed fields** for all variant-specific data 
   No opaque `dict` or `Any` fields for known data.
3. **Overrides the abstract method** to derive the output from its own typed fields — not from a pre-computed string passed at construction time.
4. **Uses a `model_validator(mode='after')`** (Pydantic v2) to derive string-serialisation fields from typed fields immediately after construction, so the instance is always self-consistent.
5. **Provides a factory classmethod** as the
   single entry point for building instances from raw data.  The classmethod owns the
   extraction/computation logic; the constructor only accepts already-resolved values.

## Concrete checklist (apply to every PR touching a domain hierarchy)

### Structure
- [ ] Is there a standalone function doing what a classmethod on the relevant variant class should do?
      → Move it into the class as `@classmethod from_X(...)`.
- [ ] Is a raw `dict` or `str` carrying data that could be a typed field on the class?
      → Add the typed field, update the constructor call, derive the string representation in the model_validator.
- [ ] Is there a giant `if/elif` dispatch block choosing between variants?
      → Replace with a registry (`dict[str, type[BaseClass]]`) + uniform factory call.
- [ ] Does a module-level function construct a base-class instance directly (`HardRule(id=..., pseudocode=...)`)
      instead of going through a concrete subclass?
      → This is a violation. Extract a concrete subclass.

### Pydantic v2 specifics
- [ ] Derived text fields (`pseudocode`, `rule_description`) computed in `model_validator(mode='after')`.
- [ ] Class-level field defaults provided for all base-class fields that are constant per variant
- [ ] No mutable default arguments (`field: list = []` — use `Field(default_factory=list)`).

### Configuration
- [ ] Rule-enabling flags and numeric thresholds live in YAML config, not hardcoded in the class body.
- [ ] The YAML config lists rule names that map 1-to-1 to class names in a registry.
      Adding a new rule = add a class + one YAML entry; nothing else changes.
- [ ] A `RuleRunner` (or equivalent orchestrator) is the *only* place that reads the YAML and
      dispatches to rule classes.  No other module reads the config directly.

## Output format when reviewing
```markdown
# Pydantic Domain-Variants Review
**Status:** 🔴 Violation found / 🟢 Compliant
```
