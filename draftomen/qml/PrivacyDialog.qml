import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Dialog {
    id: root

    property var returnFocusItem: null

    objectName: "privacyDialog"
    parent: Overlay.overlay
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    title: "Privacy"
    width: Math.min(420, Math.max(300, parent ? parent.width - 32 : 420))
    x: parent ? Math.max(16, Math.round((parent.width - width) / 2)) : 16
    y: parent ? Math.max(16, Math.round((parent.height - height) / 2)) : 16
    padding: 16

    Overlay.modal: Rectangle {
        color: "#99000000"
    }

    background: Rectangle {
        color: Theme.surface
        border.color: Theme.outline
        border.width: 1
        radius: Theme.radius
    }

    header: Rectangle {
        objectName: "privacyDialogHeader"
        implicitHeight: 52
        color: Theme.surfaceHigh
        border.color: Theme.outline
        border.width: 1

        Label {
            objectName: "privacyDialogTitle"
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            text: root.title
            color: Theme.text
            font.pixelSize: 18
            font.bold: true
            verticalAlignment: Text.AlignVCenter
            Accessible.name: text
        }
    }

    onClosed: {
        const opener = root.returnFocusItem
        root.returnFocusItem = null
        if (opener && opener.visible && opener.enabled)
            opener.forceActiveFocus()
    }

    contentItem: ColumnLayout {
        spacing: 12
        implicitWidth: 360

        Label {
            objectName: "privacyDialogDisclosure"
            Layout.fillWidth: true
            text: "All user data remains on the user's computer. Draft Omen does not send your personal data to us."
            color: Theme.text
            wrapMode: Text.WordWrap
            Accessible.name: text
        }
    }

    footer: DialogButtonBox {
        implicitHeight: 58
        alignment: Qt.AlignRight
        background: Rectangle {
            color: Theme.surfaceHigh
            border.color: Theme.outline
            border.width: 1
        }

        DimensionalButton {
            id: closeButton
            objectName: "privacyDialogCloseButton"
            text: "Close"
            accented: false
            implicitWidth: 96
            activeFocusOnTab: true
            focusPolicy: Qt.StrongFocus
            Accessible.role: Accessible.Button
            Accessible.name: "Close Privacy dialog"
            onClicked: root.close()
        }
    }
}
