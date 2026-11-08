load(
    "@rules_cc//cc:defs.bzl",
    "cc_library",
)

package(default_visibility = ["//:__subpackages__"])

cc_library(
    name = "box",
    srcs = ["box/box.cc"],
    hdrs = ["box/box.h"],
    deps = ["//lib:base"],
)
