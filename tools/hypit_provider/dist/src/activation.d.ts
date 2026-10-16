/** Installable, genuine Runtime activation; no live backend is exposed by project configuration. */
declare const _default: {
    format: "hypit.package@1";
    facets: import("@hypit/runtime-local/extension").RuntimeAdapterFacet[];
};
export default _default;
