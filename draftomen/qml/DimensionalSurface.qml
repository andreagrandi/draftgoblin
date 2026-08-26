import QtQuick 2.15

Item {
    id: root
    objectName: "dimensionalSurface"

    property bool stateEnabled: true
    property bool stateHovered: false
    property bool statePressed: false
    property bool stateFocused: false
    property bool stateSelected: false
    property bool stateExpanded: false
    property bool accented: false
    property color accentColor: Theme.primary

    readonly property real faceY: root.statePressed ? Theme.controlDepth : 0
    readonly property color fillColor: {
        if (!root.stateEnabled)
            return Theme.controlDisabled
        return root.accented ? root.accentColor : Theme.neutral
    }
    readonly property color topFillColor: {
        if (!root.stateEnabled)
            return Qt.lighter(root.fillColor, 1.04)
        if (root.statePressed)
            return Qt.darker(root.fillColor, 1.08)
        if (root.stateHovered || root.stateExpanded)
            return Qt.lighter(root.fillColor, 1.18)
        if (root.stateSelected)
            return Qt.lighter(root.fillColor, 1.13)
        return Qt.lighter(root.fillColor, 1.08)
    }
    readonly property color bottomFillColor: {
        if (!root.stateEnabled)
            return Qt.darker(root.fillColor, 1.08)
        if (root.statePressed)
            return Qt.darker(root.fillColor, 1.2)
        if (root.stateHovered || root.stateExpanded)
            return Qt.lighter(root.fillColor, 1.02)
        if (root.stateSelected)
            return Qt.darker(root.fillColor, 1.03)
        return Qt.darker(root.fillColor, 1.1)
    }
    readonly property color faceOutlineColor: {
        if (!root.stateEnabled)
            return Theme.outlineDisabled
        if (root.accented)
            return Qt.darker(root.accentColor, root.statePressed ? 1.45 : 1.3)
        if (root.stateHovered || root.stateSelected || root.stateExpanded)
            return Qt.lighter(Theme.controlNeutralBorder, 1.18)
        return Theme.controlNeutralBorder
    }
    readonly property color outlineColor: root.stateFocused
        ? Theme.focus : root.faceOutlineColor
    readonly property int outlineWidth: root.stateFocused ? 2 : 1

    Rectangle {
        id: lowerEdge
        x: 0
        y: Theme.controlDepth
        width: root.width
        height: Math.max(0, root.height - Theme.controlDepth)
        radius: Theme.controlRadius
        color: root.stateEnabled ? Theme.controlEdge : Theme.outlineDisabled
        opacity: root.statePressed ? 0 : (root.stateHovered ? 1 : 0.9)
    }

    Rectangle {
        id: face
        x: 0
        y: root.faceY
        width: root.width
        height: Math.max(0, root.height - Theme.controlDepth)
        radius: Theme.controlRadius
        border.color: root.faceOutlineColor
        border.width: 1
        gradient: Gradient {
            GradientStop {
                position: 0
                color: root.topFillColor
            }
            GradientStop {
                position: 1
                color: root.bottomFillColor
            }
        }
    }

    Rectangle {
        anchors.fill: face
        anchors.margins: 1
        radius: Math.max(0, Theme.controlRadius - 1)
        color: "transparent"
        border.color: Theme.controlHighlight
        border.width: 1
        opacity: {
            if (!root.stateEnabled)
                return 0.07
            if (root.statePressed)
                return 0.1
            if (root.stateHovered)
                return root.accented ? 0.36 : 0.24
            return root.accented ? 0.25 : 0.14
        }
    }

    Rectangle {
        x: face.x - Theme.controlFocusGap
        y: face.y - Theme.controlFocusGap
        width: face.width + Theme.controlFocusGap * 2
        height: face.height + Theme.controlFocusGap * 2
        radius: Theme.controlRadius + Theme.controlFocusGap
        color: "transparent"
        border.color: root.outlineColor
        border.width: root.outlineWidth
        visible: root.stateEnabled && root.stateFocused
    }
}
