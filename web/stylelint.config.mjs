// Stylelint config
// Standard SCSS plus BEM class names, no ids or tag selectors, max 2 nesting levels.

const bemClass =
  "^[a-z][a-z0-9]*(-[a-z0-9]+)*(__[a-z0-9]+(-[a-z0-9]+)*)?(--[a-z0-9]+(-[a-z0-9]+)*)?$";

const config = {
  extends: ["stylelint-config-standard-scss"],
  rules: {
    "selector-class-pattern": [
      bemClass,
      {
        resolveNestedSelectors: true,
        message: "Use a BEM class: block__element--modifier, lowercase kebab-case",
      },
    ],
    "selector-nested-pattern": [
      "^(?!&-(?!-))",
      { message: "Build BEM names with &__element or &--modifier, not &-suffix" },
    ],
    "selector-max-id": 0,
    "selector-max-type": 0,
    "value-keyword-case": ["lower", { ignoreProperties: ["/^--font-/"] }],
    "max-nesting-depth": [2, { ignoreAtRules: ["media", "supports"] }],
    "declaration-property-unit-allowed-list": {
      "font-size": ["rem", "em"],
      "/^(margin|padding|gap|row-gap|column-gap)/": ["rem", "em", "%"],
      "/^(width|max-width|min-width)$/": ["rem", "%", "vw", "ch"],
    },
  },
  overrides: [
    {
      files: ["src/styles/_base.scss"],
      rules: { "selector-max-type": null },
    },
  ],
};

export default config;
