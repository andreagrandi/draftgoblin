pragma Singleton

import QtQuick 2.15

QtObject {
    readonly property color background: "#050714"
    readonly property color surfaceLow: "#0b0d1c"
    readonly property color surface: "#11142a"
    readonly property color surfaceHigh: "#191d3b"
    readonly property color surfaceHighest: "#242950"
    readonly property color text: "#f5f1e8"
    readonly property color textMuted: "#c8c2b8"
    readonly property color outline: "#8b7b70"
    readonly property color primary: "#a59cff"
    readonly property color primaryDark: "#29275d"
    readonly property color warning: "#e7c993"
    readonly property color warningDark: "#4a3822"
    readonly property color error: "#ffb4ab"
    readonly property color errorDark: "#690005"
    readonly property color focus: "#d3ceff"
    readonly property color whiteMana: "#f9fafb"
    readonly property color blueMana: "#0ea5e9"
    readonly property color blackMana: "#4b5563"
    readonly property color redMana: "#ef4444"
    readonly property color greenMana: "#22c55e"

    readonly property int radius: 4
    readonly property int gutter: 12
    readonly property int panelPadding: 16
    readonly property int targetHeight: 42
    readonly property int narrowBreakpoint: 980

    function colorForMana(symbol) {
        if (symbol === "W") return whiteMana
        if (symbol === "U") return blueMana
        if (symbol === "B") return blackMana
        if (symbol === "R") return redMana
        if (symbol === "G") return greenMana
        return textMuted
    }
}

