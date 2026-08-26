import QtQuick 2.15
import QtQuick.Controls 2.15

TabButton {
    id: control

    property bool expanded: false
    property bool accented: checked
    property color accentColor: Theme.primary
    property color accentContentColor: Theme.primaryContent
    readonly property color contentColor: {
        if (!control.enabled)
            return Theme.controlDisabledContent
        return control.accented ? control.accentContentColor : Theme.neutralContent
    }

    implicitHeight: Theme.targetHeight

    background: DimensionalSurface {
        stateEnabled: control.enabled
        stateHovered: control.hovered
        statePressed: control.down
        stateFocused: control.activeFocus
        stateSelected: control.checked
        stateExpanded: control.expanded
        accented: control.accented
        accentColor: control.accentColor
    }

    contentItem: Text {
        transform: Translate {
            y: control.down ? Theme.controlPressOffset : 0
        }
        text: control.text
        color: control.contentColor
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}
